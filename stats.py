#!/usr/bin/env python

from collections import defaultdict
from glob import glob
from os.path import basename
from os.path import dirname
from os.path import expanduser
from os.path import exists
from os.path import join
from sys import path
from sys import stderr
from subprocess import call


lib = expanduser("~/OMERO.server/lib/python")
assert exists(lib)
path.insert(0, lib)

from omero import all
from omero import ApiUsageException
from omero.cli import CLI
from omero.cli import Parser
from omero.rtypes import unwrap
from omero.sys import ParametersI
from omero.util.text import TableBuilder
from omero.util.text import filesizeformat

def studies():
    rv = defaultdict(lambda: defaultdict(list))
    for study in glob("idr*"):
        if study[-1] == "/":
            study = study[0:-1]

        for screen in glob(join(study, "screen*")):
            for plate in glob(join(screen, "plates", "*")):
                rv[study][screen].append(basename(plate))
    return rv


def orphans(query):
    orphans = unwrap(query.projection((
        "select distinct f.id from Image i "
        "join i.fileset as f "
        "left outer join i.wellSamples as ws "
        "where ws = null "
        "order by f.id"), None))
    for orphan in orphans:
        print "Fileset:%s" % (orphan[0])
    print >>stderr, "Total:", len(orphans)


def unknown(query):
    on_disk = []
    for study, screens in sorted(studies().items()):
        for screen, plates in screens.items():
            on_disk.append(screen)
            on_disk.extend(plates)

    on_server = unwrap(query.projection((
        "select s.name, s.id from Screen s"), None))
    for name, id in on_server:
        if name not in on_disk:
            print "Screen:%s" % id, name


    on_server = unwrap(query.projection((
        "select s.name, p.name, p.id from Plate p "
        "join p.screenLinks as sl join sl.parent as s"), None))
    for screen, name, id in on_server:
        if name not in on_disk:
            print "Plate:%s" % id, name, screen


def check_search(query, search):
    obj_types = ('Screen', 'Plate', 'Image')
    print "loading all map annotations"
    res = query.findAllByQuery("from MapAnnotation m", None)
    all_values = set(
        v for m in res for k, v in m.getMapValueAsMap().iteritems()
    )
    print "searching for all unique values [%d]" % len(all_values)
    with open("no_matches.txt", "w") as fo:
        for v in all_values:
            try:
                matches = set()
                for t in obj_types:
                    search.onlyType(t)
                    search.byFullText(v)
                    matches.add(search.hasNext())
                if True not in matches:
                    fo.write("%s\n" % v)
            except ApiUsageException as e:
                stderr.write("%s: %s\n" % (v, e))
                continue


def stat_screens(query):

    tb = TableBuilder("Screen")
    tb.cols(["ID", "Plates", "Wells", "Images", "Planes", "Bytes"])

    plate_count = 0
    well_count = 0
    image_count = 0
    plane_count = 0
    byte_count = 0

    for study, screens in sorted(studies().items()):
        for screen, plates_expected in screens.items():
            params = ParametersI()
            params.addString("screen", screen)
            rv = unwrap(query.projection((
                "select s.id, count(distinct p.id), "
                "       count(distinct w.id), count(distinct i.id),"
                "       sum(cast(pix.sizeZ as long) * pix.sizeT * pix.sizeC), "
                "       sum(cast(pix.sizeZ as long) * pix.sizeT * pix.sizeC * pix.sizeX * pix.sizeY * 8) "
                "from Screen s "
                "left outer join s.plateLinks spl "
                "left outer join spl.child as p "
                "left outer join p.wells as w "
                "left outer join w.wellSamples as ws "
                "left outer join ws.image as i "
                "left outer join i.pixels as pix "
                "where s.name = :screen "
                "group by s.id"), params))
            if not rv:
                tb.row(screen, "MISSING", "", "", "", "", "")
            else:
                for x in rv:
                    plate_id, plates, wells, images, planes, bytes = x
                    plate_count += plates
                    well_count += wells
                    image_count += images
                    if planes: plane_count += planes
                    if bytes:
                        byte_count += bytes
                    else:
                        bytes = 0
                    if plates != len(plates_expected):
                        plates = "%s of %s" % (plates, len(plates_expected))
                    tb.row(screen, plate_id, plates, wells, images, planes, filesizeformat(bytes))
    tb.row("Total", "", plate_count, well_count, image_count, plane_count,filesizeformat(byte_count))
    print str(tb.build())


def stat_plates(query, screen):

    params = ParametersI()
    params.addString("screen", screen)

    obj = query.findByQuery((
        "select s from Screen s "
        "where s.name = :screen"), params)

    if not obj:
        raise Exception("unknown screen: %s" % screen)

    plates = glob(join(screen, "plates", "*"))
    plates = map(basename, plates)

    tb = TableBuilder("Plate")
    tb.cols(["PID", "Wells", "Images"])

    well_count = 0
    image_count = 0
    for plate in plates:
        params.addString("plate", plate)
        rv = unwrap(query.projection((
            "select p.id, count(distinct w.id), count(distinct i.id) from Screen s "
            "left outer join s.plateLinks spl join spl.child as p "
            "left outer join p.wells as w "
            "left outer join w.wellSamples as ws "
            "left outer join ws.image as i "
            "where s.name = :screen and p.name = :plate "
            "group by p.id"), params))
        if not rv:
            tb.row(plate, "MISSING", "", "")
        else:
            for x in rv:
                plate_id, wells, images = x
                well_count += wells
                image_count += images
                tb.row(plate, plate_id, wells, images)
    tb.row("Total", "", well_count, image_count)
    print str(tb.build())

def main():
    parser = Parser()
    parser.add_login_arguments()
    parser.add_argument("--orphans", action="store_true")
    parser.add_argument("--unknown", action="store_true")
    parser.add_argument("--search", action="store_true")
    parser.add_argument("screen", nargs="?")
    ns = parser.parse_args()

    cli = CLI()
    cli.loadplugins()
    client = cli.conn(ns)
    try:
        query = client.sf.getQueryService()
        if ns.orphans:
            orphans(query)
        elif ns.unknown:
            unknown(query)
        elif ns.search:
            search = client.sf.createSearchService()
            check_search(query, search)
        elif not ns.screen:
            stat_screens(query)
        else:
            stat_plates(query, ns.screen)
    finally:
        cli.close()

if __name__ == "__main__":
    main()