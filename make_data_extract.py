#!/usr/bin/python3

# Copyright (c) 2020, 2021, 2022 Humanitarian OpenStreetMap Team
#
# This file is part of Odkconvert.
#
#     Odkconvert is free software: you can redistribute it and/or modify
#     it under the terms of the GNU General Public License as published by
#     the Free Software Foundation, either version 3 of the License, or
#     (at your option) any later version.
#
#     Odkconvert is distributed in the hope that it will be useful,
#     but WITHOUT ANY WARRANTY; without even the implied warranty of
#     MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#     GNU General Public License for more details.
#
#     You should have received a copy of the GNU General Public License
#     along with Odkconvert.  If not, see <https:#www.gnu.org/licenses/>.
#

import argparse
import os
import logging
import sys
import re
import epdb
from sys import argv
from osgeo import ogr
import json
from geojson import Point, Polygon, Feature
from OSMPythonTools.overpass import Overpass
from OSMPythonTools.api import Api
from yamlfile import YamlFile


# all possible queries
choices = ["buildings", "amenities", "toilets", "landuse", "emergency", "shops", "waste", "water", "education", "healthcare"]


class OutputFile(object):
    def __init__(self, outfile=None):
        """Initialize OGR output layer"""
        outdrv = ogr.GetDriverByName("GeoJson")
        if os.path.exists(outfile):
            outdrv.DeleteDataSource(outfile)

        logging.info("Creating output data file: %s" % outfile)
        self.outdata  = outdrv.CreateDataSource(outfile)
        self.outlayer = self.outdata.CreateLayer("data", geom_type=ogr.wkbPolygon)
        self.fields = self.outlayer.GetLayerDefn()
        # newid = ogr.FieldDefn("id", ogr.OFTInteger)
        # self.outlayer.CreateField(newid)
        self.filespec = outfile

        # Read the YAML config file that tells
        self.yaml = YamlFile("filter.yaml")
        keys = list(self.yaml.yaml.keys())
        self.tags = dict()
        for key in keys:
            values = list()
            for value in self.yaml.yaml[key][0]['keep']:
                values.append(value)
            self.tags[key] = values

    def getTags(self, keyword=None):
        if keyword in self.tags:
            return self.tags[keyword]
        else:
            return None

    def addFeature(self, feature=None):
        """Add an OGR feature to the output layer"""
        self.outlayer.CreateFeature(feature)

class PostgresClient(OutputFile):
    """Class to handle SQL queries for the categories"""
    def __init__(self, dbhost=None, dbname=None, output=None):
        """Initialize the postgres handler"""
        OutputFile.__init__( self, output)
        logging.info("Opening database connection to: %s" % dbhost)
        connect = "PG: dbname=" + dbname
        connect += " host=" + dbhost
        self.pg = ogr.Open(connect)
        self.boundary = None

    def getFeature(self, boundary=None, filespec=None, category='buildings'):
        """Extract buildings from Postgres"""
        logging.info("Extracting buildings from POstgres...")

        tables = list()
        select = '*'
        if category == 'buildings':
            tables = ("ways_poly", "relations")
            filter = "tags->>'building' IS NOT NULL"
        elif category == 'amenities':
            tables = ("nodes", "ways_poly")
            filter = "tags->>'amenity' IS NOT NULL"
        elif category == 'toilets':
            tables = ("nodes", "ways_poly")
            filter = "tags->>'amenity'='toilets'"
        elif category == 'emergency':
            tables = ("nodes", "ways_poly")
            filter = "tags->>'emergency' IS NOT NULL"
        elif category == 'healthcare':
            tables = ("nodes", "ways_poly")
            select = "osm_id AS id,tags->>'name' AS title, tags->>'name' AS label, tags->>'healthcare' AS healthcareX, tags->>'healthcare:speciality' AS healthcare_speciality, tags->>'generator:source' AS generator_source, tags->'amenity' AS amenity, tags->>'operator:type' AS operator_type, geom "
            filter = "tags->>'healthcare' IS NOT NULL or tags->>'social_facility' IS NOT NULL OR tags->>'healthcare:speciality' IS NOT NULL"
        elif category == 'education':
            tables = ("nodes", "ways_poly")
            filter = "tags->>'amenity'='school' OR tags->>'amenity'='kindergarden' OR tags->>'amenity'='college' OR tags->>'amenity'='university' OR tags->>'amenity'='music_school' OR tags->>'amenity'='language_school' OR tags->>'amenity'='childcare'"
        elif category == 'shops':
            tables = ("nodes", "ways_poly")
            filter = "tags->>'shop' IS NOT NULL"
        elif category == 'waste':
            tables = ("nodes", "ways_poly")
            filter = "tags->>'amenity'='waste_disposal'"
        elif category == 'water':
            tables = ("nodes")
            filter = "tags->>'water_source' IS NOT NULL"
        elif category == 'landuse':
            tables = ("ways_poly")
            filter = "tags->>'landuse' IS NOT NULL"

        # Clip the large file using the supplied boundary
        memdrv = ogr.GetDriverByName("MEMORY")
        mem = memdrv.CreateDataSource('buildings')
        memlayer = mem.CreateLayer('buildings', geom_type=ogr.wkbMultiPolygon)
        for table in tables:
            logging.debug("Querying table %s with conditional %s" % (table, filter))
            tags = self.getTags(category)
            # osm = self.pg.GetLayerByName(table)
            query = f"SELECT {select} FROM {table} WHERE {filter}"
            logging.debug(query)
            osm = self.pg.ExecuteSQL(query)
            # osm.SetAttributeFilter(filter)
            if boundary:
                poly = ogr.Open(boundary)
                layer = poly.GetLayer()
                ogr.Layer.Clip(osm, layer, memlayer)
            else:
                memlayer = osm

            logging.info("There are %d in the %s table" % (memlayer.GetFeatureCount(), table))
            for feature in memlayer:
                poly = feature.GetGeometryRef()
                if poly is None:
                    epdb.st()
                center = poly.Centroid()
                feature.SetGeometry(center)
                self.outlayer.CreateFeature(feature)

        self.outdata.Destroy()

class OverpassClient(OutputFile):
    """Class to handle Overpass queries"""
    def __init__(self, output=None):
        """Initialize Overpass handler"""
        self.overpass = Overpass()
        OutputFile.__init__( self, output)

    def getFeature(self, boundary=None, filespec=None, category='buildings'):
        """Extract buildings from Overpass"""
        logging.info("Extracting features...")
        poly = ogr.Open(boundary)
        layer = poly.GetLayer()

        filter = None
        if category == 'buildings':
            filter = "building"
        elif category == 'amenities':
            filter = "amenities"
        elif category == 'landuse':
            filter = "landuse"
        elif category == 'healthcare':
            filter = "healthcare='*'][social_facility='*'][healthcare:speciality='*'"
        elif category == 'emergency':
            filter = "emergency"
        elif category == 'education':
            filter = "amenity=school][amenity=kindergarden"
        elif category == 'shops':
            filter = "shop"
        elif category == 'waste':
            filter = "~amenity~\"waste_*\""
        elif category == 'water':
            filter = "amenity=water_point"
        elif category == 'toilets':
            filter = "amenity=toilets"

        # Create a field in the output file for each tag from the yaml config file
        tags = self.getTags(category)
        if tags is None:
            logging.error("No data returned from Overpass!")
        if len(tags) > 0:
            for tag in tags:
                self.outlayer.CreateField(ogr.FieldDefn(tag, ogr.OFTString))
        self.fields = self.outlayer.GetLayerDefn()

        extent = layer.GetExtent()
        bbox = f"{extent[2]},{extent[0]},{extent[3]},{extent[1]}"
        query = f'(way[{filter}]({bbox}); node[{filter}]({bbox}); relation[{filter}]({bbox}); ); out body; >; out skel qt;'
        logging.debug(query)
        result = self.overpass.query(query)

        nodes = dict()
        if result.nodes() is None:
            logging.warning("No data found in this boundary!")
            return

        for node in result.nodes():
            wkt = "POINT(%f %f)" %  (float(node.lon()) , float(node.lat()))
            center = ogr.CreateGeometryFromWkt(wkt)
            nodes[node.id()] = center
        
        ways = result.ways()
        for way in ways:
            for ref in way.nodes():
                # FIXME: There's probably a better way to get the node ID.
                nd = ref._queryString.split('/')[1]
                feature = ogr.Feature(self.fields)
                feature.SetGeometry(nodes[float(nd)])
                # feature.SetField("id", way.id())
                for tag,val in way.tags().items():
                    if tag in tags:
                        feature.SetField(tag, val)
                self.addFeature(feature)
        self.outdata.Destroy()

class FileClient(OutputFile):
    """Class to handle Overpass queries"""
    def __init__(self, infile=None, output=None):
        """Initialize Overpass handler"""
        OutputFile.__init__( self, output)
        self.infile = infile

    def getFeature(self, boundary=None, infile=None, outfile=None):
        """Extract buildings from a disk file"""
        logging.info("Extracting buildings from %s..." % infile)
        if boundary:
            poly = ogr.Open(boundary)
            layer = poly.GetLayer()
            ogr.Layer.Clip(osm, layer, memlayer)
        else:
            layer = poly.GetLayer()

        tmp = ogr.Open(infile)
        layer = tmp.GetLayer()

        layer.SetAttributeFilter("tags->>'building' IS NOT NULL")

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Make GeoJson data file for ODK from OSM')
    parser.add_argument("-v", "--verbose", action="store_true", help="verbose output")
    parser.add_argument("-o", "--overpass", action="store_true", help='Use Overpass Turbo')
    parser.add_argument("-p", "--postgres", action="store_true", help='Use a postgres database')
    parser.add_argument("-g", "--geojson", default="tmp.geojson", help='Name of the GeoJson output file')
    parser.add_argument("-i", "--infile", help='Input data file')
    parser.add_argument("-dn", "--dbname", help='Database name')
    parser.add_argument("-dh", "--dbhost", default="localhost", help='Database host')
    parser.add_argument("-b", "--boundary", help='Boundary polygon to limit the data size')
    parser.add_argument("-c", "--category", default="buildings", choices=choices, help='Which category to extract')
    args = parser.parse_args()

    # if verbose, dump to the terminal.
    if args.verbose is not None:
        root = logging.getLogger()
        root.setLevel(logging.DEBUG)

        ch = logging.StreamHandler(sys.stdout)
        ch.setLevel(logging.DEBUG)
        formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        ch.setFormatter(formatter)
        root.addHandler(ch)

    if args.geojson == "tmp.geojson":
        # The data file name is in the XML file
        regex = r'jr://file.*\.geojson'
        outfile = None
        filename = args.category + ".xml"
        if not os.path.exists(filename):
            logging.error("Please run xls2xform to produce %s" % filename)
            quit()
        with open(filename, 'r') as f:
            txt = f.read()
            match = re.search(regex, txt)
            if match:
                tmp = match.group().split('/')
        outfile = tmp[3]
    else:
        outfile = args.geojson

if args.postgres:
    logging.info("Using a Postgres database for the data source")
    pg = PostgresClient(args.dbhost, args.dbname, outfile)
    pg.getFeature(args.boundary, args.geojson, args.category)
elif args.overpass:
    logging.info("Using Overpass Turbo for the data source")
    op = OverpassClient(outfile)
    op.getFeature(args.boundary, args.geojson, args.category)
elif args.infile:
    f = FileClient(args.infile)
    f.getFeature(args.boundary, args.geojson, args.category)
    logging.info("Using file %s for the data source" % args.infile)
else:
    logging.error("You need to supply either --overpass or --postgres")

logging.info("Wrote output data file to: %s" % outfile)

