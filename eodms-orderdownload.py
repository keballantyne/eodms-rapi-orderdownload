##############################################################################
# MIT License
# 
# Copyright (c) 2020-2021 Her Majesty the Queen in Right of Canada, as 
# represented by the President of the Treasury Board
# 
# Permission is hereby granted, free of charge, to any person obtaining a 
# copy of this software and associated documentation files (the "Software"), 
# to deal in the Software without restriction, including without limitation 
# the rights to use, copy, modify, merge, publish, distribute, sublicense, 
# and/or sell copies of the Software, and to permit persons to whom the 
# Software is furnished to do so, subject to the following conditions:
# 
# The above copyright notice and this permission notice shall be included in 
# all copies or substantial portions of the Software.
# 
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING 
# FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER 
# DEALINGS IN THE SOFTWARE.
# 
##############################################################################

__author__ = 'Kevin Ballantyne'
__copyright__ = 'Copyright 2020-2021 Her Majesty the Queen in Right of Canada'
__license__ = 'MIT License'
__version__ = '2.0.0'
__maintainer__ = 'Kevin Ballantyne'
__email__ = 'nrcan.eodms-sgdot.rncan@canada.ca'

import sys
import os
import re
import requests
import argparse
import traceback
import getpass
import datetime
import json
import configparser
import base64
import logging
import logging.handlers as handlers
import pathlib

from eodms_rapi import EODMSRAPI

try:
    import dateparser
except:
    msg = "Dateparser package is not installed. Please install and run script again."
    common.print_support(msg)
    logger.error(msg)
    sys.exit(1)

from utils import csv_util
from utils import image
from utils import geo

class Eodms_OrderDownload:
    
    def __init__(self, **kwargs):
        """
        Initializer for the Eodms_OrderDownload.
        
        :param kwargs: Options include:<br>
                username (str): The username of the EODMS account.<br>
                password (str): The password of the EODMS account.<br>
                downloads (str): The path where the image files will be downloaded.<br>
                results (str): The path where the results CSV files will be stored.<br>
                log (str): The path where the log file is stored.<br>
                timeout_query (float): The timeout for querying the RAPI.<br>
                timeout_order (float): The timeout for ordering in the RAPI.<br>
                max_res (int): The maximum number of results to order.<br>
                silent (boolean): False to prompt the user and print info, True to suppress it.<br>
        :type  kwargs: dict
        """
        
        self.rapi_domain = 'https://www.eodms-sgdot.nrcan-rncan.gc.ca'
        self.indent = 3

        self.operators = ['=', '<', '>', '<>', '<=', '>=', ' LIKE ', \
                        ' STARTS WITH ', ' ENDS WITH ', ' CONTAINS ', \
                        ' CONTAINED BY ', ' CROSSES ', ' DISJOINT WITH ', \
                        ' INTERSECTS ', ' OVERLAPS ', ' TOUCHES ', ' WITHIN ']
        
        self.username = kwargs.get('username')
        self.password = kwargs.get('password')
        
        self.logger = logging.getLogger('eodms')
        
        self.download_path = "downloads"
        if kwargs.get('download') is not None:
            self.download_path = str(kwargs.get('download'))
        
        self.results_path = "results"
        if kwargs.get('results') is not None:
            self.results_path = str(kwargs.get('results'))
        
        self.log_path = "log"
        if kwargs.get('log') is not None:
            self.log_path = str(kwargs.get('log'))
        
        self.timeout_query = 60.0
        if kwargs.get('timeout_query') is not None:
            self.timeout_query = float(kwargs.get('timeout_query'))
        
        self.timeout_order = 180.0
        if kwargs.get('timeout_order') is not None:
            self.timeout_order = float(kwargs.get('timeout_order'))
        
        self.max_results = 1000
        if kwargs.get('max_res') is not None:
            self.max_results = int(kwargs.get('max_res'))
        
        self.silent = False
        if kwargs.get('silent') is not None:
            self.silent = bool(kwargs.get('silent'))
        
        if self.username is not None and self.password is not None:
            self.eodms_rapi = EODMSRAPI(self.username, self.password)
        
        self.aoi_extensions = ['.gml', '.kml', '.json', '.geojson', '.shp']
        
        self.cur_res = None
            
    def _parse_dates(self, in_dates):
        """
        Parses dates from the user into a format for the EODMSRAPI
        
        :param in_dates: A string containing either a time interval 
                (24 hours, 3 months, etc.) or a range of dates 
                (20200501-20210105T054540,...)
        :type  in_date: str
                
        :return: A list of dictionaries containing keys 'start' and 'end' 
                with the specific date ranges 
                (ex: [{'start': '20200105_045034', 'end': '20210105_000000'}])
        :rtype: list
        """
        
        if in_dates is None or in_dates == '': return ''
            
        time_words = ['hour', 'day', 'week', 'month', 'year']
        
        if any(word in in_dates for word in time_words):
            start = dateparser.parse(in_dates).strftime("%Y%m%d_%H%M%S")
            end = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            dates = [{'start': start, 'end': end}]
        else:
        
            # Modify date for the EODMSRAPI object
            date_ranges = in_dates.split(',')
            
            dates = []
            for rng in date_ranges:
                start, end = rng.split('-')
                if start.lower().find('t') > -1:
                    start = start.lower().replace('t', '_')
                else:
                    start = '%s_000000' % start
                    
                if end.lower().find('t') > -1:
                    end = end.lower().replace('t', '_')
                else:
                    end = '%s_000000' % end
                
            dates.append({'start': start, 'end': end})
            
        return dates
        
    def _parse_filters(self, filters, coll_id=None):
        """
        Parses filters into a format for the EODMSRAPI
        
        :param filters: A list of filters from a user for a specific 
                collection.
        :type  filters: list
        :param coll_id: The Collection ID for the filters.
        :type  coll_id: str
                
        :return: A dictionary containing filters in a format for the 
                EODMSRAPI (ex: {"Beam Mnemonic": {'=': ['16M11', '16M13']}, 
                                "Incidence Angle": {'>': ['45.0']}).
        :rtype: dict
        """
        
        out_filters = {}
        
        for filt in filters:
            
            filt = filt.upper()
                
            if not any(x in filt for x in self.operators):
                print("Filter '%s' entered incorrectly." % filt)
                continue
            
            ops = [x for x in self.operators if x in filt]

            for o in ops:
                filt_split = filt.split(o)
                op = o
                
            if coll_id is None:
                coll_id = self.coll_id
            
            # Convert the input field for EODMS_RAPI
            key = filt_split[0].strip()
            coll_filts = self.get_filtMap()[coll_id]
            
            if not key in coll_filts.keys():
                err = "Filter '%s' is not available for Collection '%s'." \
                        % (key, coll_id)
                self.print_msg("WARNING: %s" % err)
                self.logger.warning(err)
                continue
                
            field = coll_filts[key]
            
            val = filt_split[1].strip()
            val = val.replace('"', '').replace("'", '')
            
            if val is None or val == '':
                err = "No value specified for Filter ID '%s'." % key
                self.print_msg("WARNING: %s" % err)
                self.logger.warning(err)
                continue
                
            out_filters[field] = (op, val.split('|'))
            
        return out_filters
        
    def _get_eodmsRes(self, csv_fn):
        """
        Gets the results based on a CSV file from the EODMS UI.
        
        :param csv_fn: The filename of the EODMS CSV file.
        :type  csv_fn: str
            
        :return: An ImageList object containing the images returned 
                from the EODMSRAPI.
        :rtype: image.ImageList
        """
        
        eodms_csv = csv_util.EODMS_CSV(self, csv_fn)
        csv_res = eodms_csv.import_eodmsCSV()
        
        ##################################################
        self.print_heading("Retrieving Record IDs for the list of " \
            "entries in the CSV file")
        ##################################################
        
        # Group all records into different collections
        coll_recs = {}
        for rec in csv_res:
            # Get the collection ID for the image
            collection = rec.get('collectionId')
            
            rec_lst = []
            if collection in coll_recs.keys():
                rec_lst = coll_recs[collection]
                
            rec_lst.append(rec)
            
            coll_recs[collection] = rec_lst
        
        all_res = []
        
        for coll, recs in coll_recs.items():
            
            coll_id = self.get_fullCollId(coll)
            
            filters = {}
            
            for idx in range(0, len(recs), 25):
                
                # Get the next 100 images
                if len(recs) < idx + 25:
                    sub_recs = recs[idx:]
                else:
                    sub_recs = recs[idx:25 + idx]
                    
                seq_ids = []
                
                for rec in sub_recs:
                    
                    id_val = None
                    for k in rec.keys():
                        if k.lower() in ['sequence id', 'record id', \
                            'recordid']:
                            # If the Sequence ID is in the image dictionary, 
                            #   return it as the Record ID
                            id_val = rec.get(k)
                    
                    if id_val is None:
                        # If the Order Key is in the image dictionary,
                        #   use it to query the RAPI
                        
                        order_key = rec.get('order key')
                        
                        if order_key is None or order_key == '':
                            msg = "Cannot determine record " \
                                    "ID for Result Number '%s' in the CSV file. " \
                                    "Skipping image." % rec.get('result number')
                            self.print_msg("WARNING: %s" % msg)
                            self.logger.warning(msg)
                            continue
                            
                        f = {'Order Key': ('=', [order_key])}
                        
                        # Send a query to the EODMSRAPI object
                        self.eodms_rapi.search(coll_id, f)
                        
                        res = self.eodms_rapi.get_results()
                        
                        if len(res) > 1:
                            msg = "Cannot determine record " \
                                    "ID for Result Number '%s' in the CSV file. " \
                                    "Skipping image." % rec.get('result number')
                            self.print_msg("WARNING: %s" % msg)
                            self.logger.warning(msg)
                            continue
                        
                        all_res += res
                        
                        continue
                        
                    seq_ids.append(id_val)
                    
                if len(seq_ids) == 0: continue
                
                filters['Sequence Id'] = ('=', seq_ids)
                    
                if coll == 'NAPL':
                    filters['Price'] = ('=', True)
                        
                # Send a query to the EODMSRAPI object
                self.eodms_rapi.search(coll, query=filters)
                
                res = self.eodms_rapi.get_results()
                
                # If the results is a list, an error occurred
                if res is None:
                    self.print_msg("WARNING: %s" % ' '.join(res))
                    self.logger.warning(' '.join(res))
                    continue
                
                # If no results, return as error
                if len(res) == 0:
                    err = "No images could be found."
                    common.print_msg("WARNING: %s" % err)
                    self.logger.warning(err)
                    common.print_msg("Skipping this entry", False)
                    self.logger.warning("Skipping this entry")
                    continue
                
                all_res += res
        
        # Convert results to ImageList
        self.results = image.ImageList(self)
        self.results.ingest_results(all_res)
        
        return self.results
        
    def _get_prevRes(self, csv_fn):
        """
        Creates a EODMSRAPI instance.
        
        :param csv_fn: The filename of the previous results CSV file.
        :type  csv_fn: str
        
        :return: A list of rows from the CSV file.
        :rtype: list
        """
        
        eodms_csv = csv_util.EODMS_CSV(self, csv_fn)
        csv_res = eodms_csv.import_csv()
        
        # Convert results to ImageList
        query_imgs = image.ImageList(self)
        query_imgs.ingest_results(csv_res, True)
        
        return query_imgs
        
    def _print_results(self, images):
        """
        Prints the results of image downloads.
        
        :param images: A list of images after they've been downloaded.
        :type  images: list
        """
        
        success_orders = []
        failed_orders = []
        
        for img in images.get_images():
            if img.get_metadata('status') == 'AVAILABLE_FOR_DOWNLOAD':
                success_orders.append(img)
            else:
                failed_orders.append(img)
        
        if len(success_orders) > 0:
            # Print information for all successful orders
            #   including the download location
            msg = "The following images have been downloaded:\n"
            for img in success_orders:
                print("img metadata: %s" % img.get_metadata())
                rec_id = img.get_recordId()
                order_id = img.get_metadata('orderId')
                orderitem_id = img.get_metadata('itemId')
                dests = img.get_metadata('downloadPaths')
                for d in dests:
                    loc_dest = d['local_destination']
                    src_url = d['url']
                    msg += "\nRecord ID %s\n" % rec_id
                    msg += "    Order Item ID: %s\n" % orderitem_id
                    msg += "    Order ID: %s\n" % order_id
                    msg += "    Downloaded File: %s\n" % loc_dest
                    msg += "    Source URL: %s\n" % src_url
            self.print_footer('Successful Downloads', msg)
            self.logger.info("Successful Downloads: %s" % msg)
        
        if len(failed_orders) > 0:
            msg = "The following images did not download:\n"
            for img in failed_orders:
                rec_id = img.get_recordId()
                order_id = img.get_metadata('orderId')
                orderitem_id = img.get_metadata('itemId')
                status = img.get_metadata('status')
                stat_msg = img.get_metadata('statusMessage')
                
                msg += "\nRecord ID %s\n" % rec_id
                msg += "    Order Item ID: %s\n" % orderitem_id
                msg += "    Order ID: %s\n" % order_id
                msg += "    Status: %s\n" % status
                msg += "    Status Message: %s\n" % stat_msg
            self.print_footer('Failed Downloads', msg)
            self.logger.info("Failed Downloads: %s" % msg)
        
    def convert_date(self, in_date):
        """
        Converts a date to ISO standard format.
        
        :param in_date: A string containing a date in format YYYYMMDD.
        :type  in_date: str
        
        :return: The date converted to ISO format.
        :rtype: str
        """
        
        if in_date.lower().find('t') > -1:
            date, tme = in_date.lower().split('t')
            year = date[:4]
            mth = date[4:6]
            day = date[6:]
            hour = tme[:2]
            minute = tme[2:4]
            sec = tme[4:]
            out_date = '%s-%s-%sT%s:%s:%sZ' % (year, mth, day, hour, minute, sec)
        else:
            year = in_date[:4]
            mth = in_date[4:6]
            day = in_date[6:]
            out_date = '%s-%s-%sT00:00:00Z' % (year, mth, day)
                    
        return out_date
        
    def create_session(self, username, password):
        """
        Creates a EODMSRAPI instance.
        
        :param username: The EODMS username of the user account.
        :type  username: str
        :param password: The EODMS password of the user account.
        :type  password: str
        """
        
        self.username = username
        self.password = password
        self.eodms_rapi = EODMSRAPI(username, password)
        
    def export_results(self):
        """
        Exports results to a CSV file.
        """
        
        if self.cur_res is None: return None
        
        # Create EODMS_CSV object to export results
        res_fn = os.path.join(self.results_path, \
                "%s_Results.csv" % self.fn_str)
        res_csv = csv_util.EODMS_CSV(self, res_fn)
        
        res_csv.export_results(self.cur_res)
        
        msg = "Results exported to '%s'." % res_fn
        self.print_msg(msg, indent=False)
        
    def export_records(self, csv_f, header, records):
        """
        Exports a set of records to a CSV.
        
        :param csv_f: The CSV file to write to.
        :type  csv_f: (file object)
        :param header: A list containing the header for the file.
        :type  header: list
        :param records: A list of images.
        :type  records: list
        """
        
        # Write the values to the output CSV file
        for rec in records:
            out_vals = []
            for h in header:
                if h in rec.keys():
                    val = str(rec[h])
                    if val.find(',') > -1:
                        val = '"%s"' % val
                    out_vals.append(val)
                else:
                    out_vals.append('')
                    
            out_vals = [str(i) for i in out_vals]
            csv_f.write('%s\n' % ','.join(out_vals))
            
    def get_collIdByName(self, in_title): #, unsupported=False):
        """
        Gets the Collection ID based on the tile/name of the collection.
        
        :param in_title: The title/name of the collection. (ex: 'RCM Image Products' for ID 'RCMImageProducts')
        :type  in_title: str
        
        :return: The full Collection ID.
        :rtype: str
        """
        
        if isinstance(in_title, list):
            in_title = in_title[0]
        
        for k, v in self.eodms_rapi.get_collections().items():
            if v['title'].find(in_title) > -1:
                return k
                
        return self.get_fullCollId(in_title)
        
    def get_fullCollId(self, coll_id):
        """
        Gets the full collection ID using the input collection ID which can be a 
            substring of the collection ID.
        
        :param coll_id: The collection ID to check.
        :type  coll_id: str
            
        :return: The full Collection ID.
        :rtype: str
        """
        
        collections = self.eodms_rapi.get_collections()
        for k, v in collections.items():
            if k.find(coll_id) > -1 or v['title'].find(coll_id) > -1:
                return k
                
    def retrieve_orders(self, query_imgs):
        """
        Retrieves existing orders based on a list of images.
        
        :param query_imgs: An ImageList containing the images.
        :type  query_imgs: image.ImageList
        
        :return: An OrderList containing the orders
        :rtype: image.OrderList
        """
        
        json_res = query_imgs.get_raw()
        
        # Get existing orders of the images
        order_res = self.eodms_rapi.get_ordersByRecords(json_res)
        
        # Convert results to an OrderList
        orders = image.OrderList(self, query_imgs)
        orders.ingest_results(order_res)
        
        if orders.count_items() == 0:
            # If no order are found...
            if self.silent:
                print("\nNo previous orders could be found.")
                # Export polygons of images
                eodms_geo = geo.Geo()
                eodms_geo.export_results(query_imgs, self.output)
                self.export_results()
                self.print_support()
                self.logger.info("No previous orders could be found.")
                sys.exit(0)
            else:
                # Ask user if they'd like to order the images
                msg = "\nNo existing orders could be found for the given AOI. " \
                        "Would you like to order the images? (y/n): "
                answer = input(msg)
                if answer.lower().find('y') > -1:
                    order_res = self.eodms_rapi.order(json_res)
                else:
                    # Export polygons of images
                    eodms_geo = geo.Geo()
                    eodms_geo.export_results(query_imgs, self.output)
                    
                    self.export_results()
                    self.print_support()
                    self.logger.info("Process ended by user.")
                    sys.exit(0)
                    
                orders.ingest_results(order_res)
        
        # Update the self.cur_res for output results
        self.cur_res = query_imgs
        
        return orders

    def is_json(self, my_json):
        """
        Checks if the input item is in JSON format.
        
        :param my_json: A string value from the requests results.
        :type  my_json: str
        
        :return: True if the input string is in valid JSON format, False if not.
        :rtype: boolean
        """
        try:
            json_object = json.loads(my_json)
        except (ValueError, TypeError) as e:
            return False
        return True
        
    def sort_fields(self, fields):
        """
        Sorts a list of fields to include recordId, collectionId
        
        :param fields: A list of fields from an Image.
        :type  fields: list
        
        :return: The sorted list of fields.
        :rtype: list
        """
        
        field_order = ['recordId', 'collectionId']
        
        if 'orderId' in fields: field_order.append('orderId')
        if 'itemId' in fields: field_order.append('itemId')
        
        out_fields = field_order
        
        for f in fields:
            if f not in field_order:
                out_fields.append(f)
        
        return out_fields
        
    def parse_max(self, maximum):
        """
        Parses the maximum values entered by the user
        
        :param maximum: The maximum value(s) entered by the user.
        :type  maximum: str
        
        :return: The maximum number of images to order and the total number of images per order.
        :rtype: tuple
        """
        
        # Parse the maximum number of orders and items per order
        max_items = None
        max_images = None
        if maximum is not None:
            if maximum.find(':') > -1:
                max_images, max_items = maximum.split(':')
            else:
                max_items = None
                max_images = maximum
                
        if max_images:
            max_images = int(max_images)
            
        if max_items:
            max_items = int(max_items)
            
        return (max_images, max_items)
        
    def print_msg(self, msg, nl=True, indent=True):
        """
        Prints a message to the command prompt.
        
        :param msg: The message to print to the screen.
        :type  msg: str
        :param nl: If True, a newline will be added to the start of the message.
        :type  nl: boolean
        :param indent: A string with the indentation.
        :type  indent: str
        """
        
        indent_str = ''
        if indent:
            indent_str = ' '*self.indent
        if nl: msg = "\n%s%s" % (indent_str, msg)
        else: msg = "%s%s" % (indent_str, msg)
        
        print(msg)
        
    def print_footer(self, title, msg):
        """
        Prints a footer to the command prompt.
        
        :param title: The title of the footer.
        :type  title: str
        :param msg: The message for the footer.
        :type  msg: str
        """
        
        print("\n%s-----%s%s" % (' '*self.indent, title, str((59 - len(title))*'-')))
        msg = msg.strip('\n')
        for m in msg.split('\n'):
            print("%s| %s" % (' '*self.indent, m))
        print("%s--------------------------------------------------------------" \
                "--" % str(' '*self.indent))
        
    def print_heading(self, msg):
        """
        Prints a heading to the command prompt.
        
        :param msg: The msg for the heading.
        :type  msg: str
        """
        
        print("\n**************************************************************" \
                "************")
        print(" %s" % msg)
        print("****************************************************************" \
                "**********")
        
    def print_support(self, err_str=None):
        """
        Prints the 2 different support message depending if an error occurred.
        
        :param err_str: The error string to print along with support.
        :type  err_str: str
        """
        
        if err_str is None:
            print("\nIf you have any questions or require support, " \
                    "please contact the EODMS Support Team at " \
                    "nrcan.eodms-sgdot.rncan@canada.ca")
        else:
            print("\nERROR: %s" % err_str)
            
            print("\nExiting process.")
            
            print("\nFor help, please contact the EODMS Support Team at " \
                    "nrcan.eodms-sgdot.rncan@canada.ca")
                    
    def get_filtMap(self):
        """
        Gets the dictionary containing the field IDs for RAPI query.
        
        :return: A dictionary containing a mapping of the English field name to the fied ID.
        :rtype: dict
        """
        
        return {
            'COSMO-SkyMed1':
                {
                    'ORBIT_DIRECTION': 'Absolute Orbit', 
                    'PIXEL_SPACING': 'Spatial Resolution'
                }, 
            'DMC':
                {
                    'CLOUD_COVER': 'Cloud Cover', 
                    'PIXEL_SPACING': 'Spatial Resolution', 
                    'INCIDENCE_ANGLE': 'Incidence Angle'
                }, 
            'Gaofen-1':
                {
                    'CLOUD_COVER': 'Cloud Cover', 
                    'PIXEL_SPACING': 'Spatial Resolution', 
                    'INCIDENCE_ANGLE': 'Sensor Incidence Angle'
                }, 
            'GeoEye-1':
                {
                    'CLOUD_COVER': 'Cloud Cover', 
                    'PIXEL_SPACING': 'Spatial Resolution', 
                    'INCIDENCE_ANGLE': 'Sensor Incidence Angle', 
                    'SENSOR_MODE': 'Sensor Mode'
                }, 
            'IKONOS': 
                {
                    'CLOUD_COVER': 'Cloud Cover', 
                    'PIXEL_SPACING': 'Spatial Resolution', 
                    'INCIDENCE_ANGLE': 'Sensor Incidence Angle', 
                    'SENSOR_MODE': 'Sensor Mode'
                }, 
            'IRS': 
                {
                    'CLOUD_COVER': 'Cloud Cover', 
                    'PIXEL_SPACING': 'Spatial Resolution', 
                    'INCIDENCE_ANGLE': 'Sensor Incidence Angle', 
                    'SENSOR_MODE': 'Sensor Mode'
                }, 
            'NAPL':
                {
                    'COLOUR': 'Sensor Mode', 
                    'SCALE': 'Scale', 
                    'ROLL': 'Roll Number', 
                    'PHOTO_NUMBER': 'Photo Number' 
                    # 'PREVIEW_AVAILABLE': 'PREVIEW_AVAILABLE'
                }, 
            'PlanetScope': 
                {
                    'CLOUD_COVER': 'Cloud Cover', 
                    'PIXEL_SPACING': 'Spatial Resolution', 
                    'INCIDENCE_ANGLE': 'Sensor Incidence Angle'
                }, 
            'QuickBird-2': 
                {
                    'CLOUD_COVER': 'Cloud Cover', 
                    'PIXEL_SPACING': 'Spatial Resolution', 
                    'INCIDENCE_ANGLE': 'Sensor Incidence Angle', 
                    'SENSOR_MODE': 'Sensor Mode'
                }, 
            'RCMImageProducts': 
                {
                    'ORBIT_DIRECTION': 'Orbit Direction', 
                    # 'INCIDENCE_ANGLE': 'SENSOR_BEAM_CONFIG.INCIDENCE_LOW,SENSOR_BEAM_CONFIG.INCIDENCE_HIGH', 
                    'PIXEL_SPACING': 'Spatial Resolution', 
                    'INCIDENCE_ANGLE': 'Incidence Angle', 
                    'BEAM_MNEMONIC': 'Beam Mnemonic', 
                    'BEAM_MODE_QUALIFIER': 'Beam Mode Qualifier', 
                    # 'BEAM_MODE_TYPE': 'RCM.SBEAM',
                    'DOWNLINK_SEGMENT_ID': 'Downlink Segment ID', 
                    'LUT_Applied': 'LUT Applied', 
                    'OPEN_DATA': 'Open Data', 
                    'POLARIZATION': 'Polarization', 
                    'PRODUCT_FORMAT': 'Product Format', 
                    'PRODUCT_TYPE': 'Product Type', 
                    'RELATIVE_ORBIT': 'Relative Orbit', 
                    'WITHIN_ORBIT_TUBE': 'Within Orbit Tube', 
                    'ORDER_KEY': 'Order Key', 
                    'SEQUENCE_ID': 'Sequence Id', 
                    'SPECIAL_HANDLING_REQUIRED': 'Special Handling Required'
                }, 
            'RCMScienceData': 
                {
                    'ORBIT_DIRECTION': 'Orbit Direction', 
                    'INCIDENCE_ANGLE': 'Incidence Angle', 
                    'BEAM_MODE': 'Beam Mode Type', 
                    'BEAM_MNEMONIC': 'Beam Mnemonic', 
                    'TRANSMIT_POLARIZATION': 'Transmit Polarization', 
                    'RECEIVE POLARIZATION': 'Receive Polarization', 
                    'DOWNLINK_SEGMENT_ID': 'Downlink Segment ID'

                }, 
            'Radarsat1': 
                {
                    'ORBIT_DIRECTION': 'Orbit Direction',
                    'PIXEL_SPACING': 'Spatial Resolution', 
                    # 'INCIDENCE_ANGLE': 'SENSOR_BEAM_CONFIG.INCIDENCE_LOW,SENSOR_BEAM_CONFIG.INCIDENCE_HIGH', 
                    'INCIDENCE_ANGLE': 'Incidence Angle', 
                    # 'BEAM_MODE': 'RSAT1.SBEAM', 
                    'BEAM_MNEMONIC': 'Position', 
                    'ORBIT': 'Absolute Orbit'
                }, 
            'Radarsat1RawProducts': 
                {
                    'ORBIT_DIRECTION': 'Orbit Direction',
                    'PIXEL_SPACING': 'Spatial Resolution', 
                    'INCIDENCE_ANGLE': 'Incidence Angle', 
                    'DATASET_ID': 'Dataset Id', 
                    'ARCHIVE_FACILITY': 'Reception Facility', 
                    'RECEPTION FACILITY': 'Reception Facility', 
                    'BEAM_MODE': 'Sensor Mode', 
                    'BEAM_MNEMONIC': 'Position', 
                    'ABSOLUTE_ORBIT': 'Absolute Orbit'
                }, 
            'Radarsat2':
                {
                    'ORBIT_DIRECTION': 'Orbit Direction', 
                    'PIXEL_SPACING': 'Spatial Resolution', 
                    # 'INCIDENCE_ANGLE': 'SENSOR_BEAM_CONFIG.INCIDENCE_LOW,SENSOR_BEAM_CONFIG.INCIDENCE_HIGH', 
                    'INCIDENCE_ANGLE': 'Incidence Angle', 
                    'SEQUENCE_ID': 'Sequence Id', 
                    # 'BEAM_MODE': 'RSAT2.SBEAM', 
                    'BEAM_MNEMONIC': 'Position', 
                    'LOOK_DIRECTION': 'Look Direction', 
                    'TRANSMIT_POLARIZATION': 'Transmit Polarization', 
                    'RECEIVE_POLARIZATION': 'Receive Polarization', 
                    'IMAGE_ID': 'Image Id', 
                    'RELATIVE_ORBIT': 'Relative Orbit', 
                    'ORDER_KEY': 'Order Key'
                }, 
            'Radarsat2RawProducts': 
                {
                    'ORBIT_DIRECTION': 'Orbit Direction', 
                    'PIXEL_SPACING': 'Spatial Resolution', 
                    'INCIDENCE_ANGLE': 'Incidence Angle', 
                    'LOOK_ORIENTATION': 'Look Orientation', 
                    'BEAM_MODE': 'Sensor Mode', 
                    'BEAM_MNEMONIC': 'Position', 
                    'TRANSMIT_POLARIZATION': 'Transmit Polarization', 
                    'RECEIVE_POLARIZATION': 'Receive Polarization', 
                    'IMAGE_ID': 'Image Id'
                }, 
            'RapidEye': 
                {
                    'CLOUD_COVER': 'Cloud Cover', 
                    'PIXEL_SPACING': 'Spatial Resolution', 
                    'INCIDENCE_ANGLE': 'Sensor Incidence Angle', 
                    'SENSOR_MODE': 'Sensor Mode'
                }, 
            'SGBAirPhotos': 
                {
                    'SCALE': 'Scale', 
                    'ROLL_NUMBER': 'Roll Number', 
                    'PHOTO_NUMBER': 'Photo Number', 
                    'AREA': 'Area'
                }, 
            'SPOT': 
                {
                    'CLOUD_COVER': 'Cloud Cover', 
                    'PIXEL_SPACING': 'Spatial Resolution', 
                    'INCIDENCE_ANGLE': 'Sensor Incidence Angle'
                }, 
            'TerraSarX': 
                {
                    'ORBIT_DIRECTION': 'Orbit Direction', 
                    'PIXEL_SPACING': 'Spatial Resolution', 
                    'INCIDENCE_ANGLE': 'Incidence Angle'
                }, 
            'VASP': 
                {
                    'VASP_OPTIONS': 'Sequence Id'
                }, 
            'WorldView-1': 
                {
                    'CLOUD_COVER': 'Cloud Cover', 
                    'PIXEL_SPACING': 'Spatial Resolution', 
                    'INCIDENCE_ANGLE': 'Sensor Incidence Angle', 
                    'SENSOR_MODE': 'Sensor Mode'
                }, 
            'WorldView-2': 
                {
                    'CLOUD_COVER': 'Cloud Cover', 
                    'PIXEL_SPACING': 'Spatial Resolution', 
                    'INCIDENCE_ANGLE': 'Sensor Incidence Angle', 
                    'SENSOR_MODE': 'Sensor Mode'
                }, 
            'WorldView-3': 
                {
                    'CLOUD_COVER': 'Cloud Cover', 
                    'PIXEL_SPACING': 'Spatial Resolution', 
                    'INCIDENCE_ANGLE': 'Sensor Incidence Angle', 
                    'SENSOR_MODE': 'Sensor Mode'
                }
            }
    
    def query_entries(self, collections, **kwargs):
        """
        Sends various image entries to the EODMSRAPI.
        
        :param collection: A list of collections.
        :type  collection: list
        :param kwarg: A dictionary of arguments:
        
                - filters (dict): A dictionary of filters separated by 
                    collection.
                - aoi (str): The filename of the AOI.
                - dates (list): A list of date ranges 
                    ([{'start': <date>, 'end': <date>}]).
                - max_images (int): The maximum number of images to query.
        :type  kwarg: dict
        
        :return: The ImageList object containing the results of the query.
        :rtype: image.ImageList
        """
        
        filters = kwargs.get('filters')
        aoi = kwargs.get('aoi')
        dates = kwargs.get('dates')
        max_images = kwargs.get('max_images')
        
        feats = [('INTERSECTS', aoi)]
        
        all_res = []
        for coll in collections:
            
            # Get the full Collection ID
            self.coll_id = self.get_fullCollId(coll)
            
            # Parse filters
            if filters:
                if self.coll_id in filters.keys():
                    coll_filts = filters[self.coll_id]
                    filters = self._parse_filters(coll_filts)
                    if isinstance(filters, str):
                        filters = None
                else:
                    filters = None
            else:
                filters = None
                
            if self.coll_id == 'NAPL':
                filters = {}
                filters['Price'] = ('=', True)
            
            # Send a query to the EODMSRAPI object
            self.eodms_rapi.search(self.coll_id, filters, feats, dates, \
                maxResults=max_images)
            
            res = self.eodms_rapi.get_results()
            
            # Add this collection's results to all results
            all_res += res
            
        # Convert results to ImageList
        query_imgs = image.ImageList(self)
        query_imgs.ingest_results(all_res)
        
        return query_imgs
    
    def set_attempts(self, attempts):
        """
        Sets the number of attempts for query the EODMSRAPI.
        
        :param attempts: The number of attempts.
        :type  attempts: str or int
        """
        
        try:
            self.attempts = int(attempts)
        except ValueError:
            self.attempts = 4
    
    def log_parameters(self, params, title=None):
        """
        Logs the script parameters in the log file.
        
        :param params: A dictionary of the script parameters.
        :type  params: dict
        :param title: The title of the message.
        :type  title: str
        """
        
        if title is None: title = "Script Parameters"
        
        msg = "%s:\n" % title
        for k, v in params.items():
            msg += "  %s: %s\n" % (k, v)
        self.logger.info(msg)
        
    def set_silence(self, silent):
        """
        Sets the silence of the script.
        
        :param silent: Determines whether the script will be silent. If True, the user is not prompted for info.
        :type  silent: boolean
        """
        
        self.silent = silent
        
    def validate_collection(self, coll):
        """
        Checks if the Collection entered by the user is valid.
        
        :param coll: The Collection value to check.
        :type  coll: str
        
        :return: Returns the Collection if valid, False if not.
        :rtype: str or boolean
        """
        
        colls = self.eodms_rapi.get_collections()
        
        aliases = [v['aliases'] for v in colls.values()]
        coll_vals = list(colls.keys()) + [v['title'] for v in \
                    colls.values()]
        for a in aliases:
            coll_vals += a
        
        if coll.lower() in [c.lower() for c in coll_vals]:
            return True
            
        return False
        
    def validate_dates(self, dates):
        """
        Checks if the date entered by the user is valid.
        
        :param dates: A range of dates or time interval.
        :type  dates: str
        
        :return: Returns the dates if valid, False if not.
        :rtype: str or boolean
        """
        
        try:
            self._parse_dates(dates)
            return dates
        except:
            return False
        
    def validate_int(self, val, limit=None):
        """
        Checks if the number entered by the user is valid.
        
        :param val: A string (or integer) of an integer.
        :type  val: str or int
        :param limit: A number to check whether the val is less than a certain limit.
        :type  limit: int
        
        :return: Returns the val if valid, False if not.
        :rtype: str or boolean
        """
        
        try:
            if isinstance(val, str):
                if val == '':
                    return None
                val = int(val)
            
            if isinstance(val, list):
                if limit is not None:
                    if any(int(v) > limit for v in val):
                        err_msg = "WARNING: One of the values entered is " \
                            "invalid."
                        self.print_msg(err_msg, indent=False)
                        self.logger.warning(err_msg)
                        return False
                    out_val = [int(v) for v in val]
                else:
                    out_val = int(val)
            else:
                if limit is not None:
                    if int(val) > limit:
                        err_msg = "WARNING: The values entered are invalid."
                        self.print_msg(err_msg, indent=False)
                        self.logger.warning(err_msg)
                        return False
                
                out_val = int(val)
                    
            return out_val
            
        except ValueError:
            err_msg = "WARNING: Not a valid entry."
            self.print_msg(err_msg, indent=False)
            self.logger.warning(err_msg)
            return False
            
    def validate_file(self, in_fn, aoi=False):
        """
        Checks if a file name entered by the user is valid.
        
        :param in_fn: The filename of the input file.
        :type  in_fn: str
        :param aoi: Determines whether the file is an AOI.
        :type  aoi: boolean
        
        :return: If the file is invalid (wrong format or does not exist), False is returned. Otherwise the original filename is returned.
        :rtype: str or boolean
        """
        
        abs_path = os.path.abspath(in_fn)
        
        if aoi:
            if not any(s in in_fn for s in self.aoi_extensions):
                err_msg = "The AOI file is not a valid file. Please make " \
                            "sure the file is either a GML, KML, GeoJSON " \
                            "or Shapefile."
                self.print_support(err_msg)
                self.logger.error(err_msg)
                return False
        
            if not os.path.exists(abs_path):
                err_msg = "The AOI file does not exist."
                self.print_support(err_msg)
                self.logger.error(err_msg)
                return False
                
        if not os.path.exists(abs_path):
            return False
            
        return abs_path
        
    def validate_filters(self, filt_items, coll_id):
        """
        Checks if a list of filters entered by the user is valid.
        
        :param filt_items: A list of filters entered by the user for a given collection.
        :type  filt_items: list
        :param coll_id: The Collection ID of the filter.
        :type  coll_id: str
        
        :return: If one of the filters is invalid, False is returned. Otherwise the original filters are returned.
        :rtype: boolean or str
        """
        
        # Check if filter has proper operators
        if not any(x in filt_items.upper() for x in self.operators):
            err_msg = "Filter(s) entered incorrectly. Make sure each " \
                        "filter is in the format of <filter_id><operator>" \
                        "<value>[|<value>] and each filter is separated by " \
                        "a comma."
            self.print_support(err_msg)
            self.logger.error(err_msg)
            return False
            
        # Check if filter name is valid
        coll_filts = self.get_filtMap()[coll_id]
        filts = filt_items.split(',')
        
        for f in filts:
            if not any(x in f.upper() for x in coll_filts.keys()):
                err_msg = "Filter '%s' is not available for collection " \
                            "'%s'." % (f, coll_id)
                self.print_support(err_msg)
                self.logger.error(err_msg)
                return False
                
        return filt_items
        
    def search_orderDownload(self, params):
        """
        Runs all steps: querying, ordering and downloading
        
        :param params: A dictionary containing the arguments and values.
        :type  params: dict
        """
        
        # Log the parameters
        self.log_parameters(params)
        
        # Get all the values from the parameters
        collections = params.get('collections')
        dates = params.get('dates')
        aoi = params.get('input')
        filters = params.get('filters')
        process = params.get('process')
        maximum = params.get('maximum')
        self.output = params.get('output')
        priority = params.get('priority')
        
        # Validate AOI
        aoi_check = self.validate_file(aoi, True)
        if not aoi_check:
            err_msg = "The provided input file is not a valid AOI " \
                        "file. Exiting process."
            self.print_support()
            self.logger.error(err_msg)
            sys.exit(1)
            
        # Create info folder, if it doesn't exist, to store CSV files
        start_time = datetime.datetime.now()
        start_str = start_time.strftime("%Y-%m-%d %H:%M:%S")
        self.fn_str = start_time.strftime("%Y%m%d_%H%M%S")
        
        self.logger.info("Process start time: %s" % start_str)
        
        #############################################
        # Search for Images
        #############################################
        
        # Parse maximum items
        max_images, max_items = self.parse_max(maximum)
        
        # Convert collections to list if not already
        if not isinstance(collections, list):
            collections = [collections]
            
        # Parse dates if not already done
        if not isinstance(dates, list):
            dates = self._parse_dates(dates)
            
        # Send query to EODMSRAPI
        query_imgs = self.query_entries(collections, filters=filters, \
            aoi=aoi, dates=dates, max_images=max_images)
            
        # If no results were found, inform user and end process
        if query_imgs.count() == 0:
            msg = "Sorry, no results found for given AOI."
            self.print_msg(msg)
            self.print_msg("Exiting process.")
            self.print_support()
            self.logger.warning(msg)
            sys.exit(1)
            
        # Update the self.cur_res for output results
        self.cur_res = query_imgs
        
        # Print results info
        msg = "%s images returned from search results.\n" % query_imgs.count()
        self.print_footer('Query Results', msg)
        
        if max_images is None or max_images == '':
            # Inform the user of the total number of found images and ask if 
            #   they'd like to continue
            if not self.silent:
                answer = input("\n%s images found intersecting your AOI. " \
                            "Proceed with ordering? (y/n): " % \
                            query_imgs.count())
                if answer.lower().find('n') > -1:
                    self.export_results()
                    print("Exiting process.")
                    self.print_support()
                    self.logger.info("Process stopped by user.")
                    sys.exit(0)
        else:
            # If the user specified a maximum number of orders, 
            #   trim the results
            if len(collections) == 1:
                self.print_msg("Proceeding to order and download the first %s " \
                    "images." % max_images)
                query_imgs.trim(max_images)
            else:
                self.print_msg("Proceeding to order and download the first %s " \
                    "images from each collection." % max_images)
                query_imgs.trim(max_images, collections)
            
        #############################################
        # Order Images
        #############################################
        
        # Convert results to JSON
        json_res = query_imgs.get_raw()
        
        # Convert results to an OrderList
        orders = image.OrderList(self, query_imgs)
        
        # Send orders to the RAPI
        if max_items is None or max_items == 0:
            # Order all images in a single order
            order_res = self.eodms_rapi.order(json_res, priority)
            orders.ingest_results(order_res)
        else:
            # Divide the images into the specified number of images per order
            for idx in range(0, len(json_res), max_items):
                # Get the next 100 images
                if len(json_res) < idx + max_items:
                    sub_recs = json_res[idx:]
                else:
                    sub_recs = json_res[idx:max_items + idx]
                    
                order_res = self.eodms_rapi.order(sub_recs, priority)
                orders.ingest_results(order_res)
                
        # Update the self.cur_res for output results
        self.cur_res = query_imgs
        
        if orders.count_items() == 0:
            # If no orders could be found
            self.export_results()
            err_msg = "No orders were submitted successfully."
            self.print_support(err_msg)
            self.logger.error(err_msg)
            sys.exit(1)
        
        #############################################
        # Download Images
        #############################################
        
        # Get a list of order items in JSON format for the EODMSRAPI
        items = orders.get_raw()
        
        # Make the download folder if it doesn't exist
        if not os.path.exists(self.download_path):
            os.mkdir(self.download_path)
        
        # Download images using the EODMSRAPI
        download_items = self.eodms_rapi.download(items, self.download_path)
        
        # Update the images with the download info
        query_imgs.update_downloads(download_items)
        
        self._print_results(query_imgs)
        
        eodms_geo = geo.Geo()
        eodms_geo.export_results(query_imgs, self.output)
        
        # Update the self.cur_res for output results
        self.cur_res = query_imgs
        
        self.export_results()
        
        end_time = datetime.datetime.now()
        end_str = end_time.strftime("%Y-%m-%d %H:%M:%S")
        
        self.logger.info("End time: %s" % end_str)
        
    def order_csv(self, params):
        """
        Orders and downloads images using the CSV exported from the EODMS UI.
        
        :param params: A dictionary containing the arguments and values.
        :type  params: dict
        """
        
        csv_fn = params.get('input')
        maximum = params.get('maximum')
        priority = params.get('priority')
        self.output = params.get('output')
        
        # Log the parameters
        self.log_parameters(params)
        
        if csv_fn.find('.csv') == -1:
            err_msg = "The provided input file is not a CSV file. " \
                        "Exiting process."
            self.print_support(err_msg)
            self.logger.error(err_msg)
            sys.exit(1)
        
        # Create info folder, if it doesn't exist, to store CSV files
        start_time = datetime.datetime.now()
        start_str = start_time.strftime("%Y-%m-%d %H:%M:%S")
        self.fn_str = start_time.strftime("%Y%m%d_%H%M%S")
        folder_str = start_time.strftime("%Y-%m-%d")
        
        self.logger.info("Process start time: %s" % start_str)
        
        #############################################
        # Search for Images
        #############################################
        
        self.eodms_rapi.get_collections()
        
        # Parse the maximum number of orders and items per order
        max_images, max_items = self.parse_max(maximum)
        
        # Import and query entries from the CSV
        query_imgs = self._get_eodmsRes(csv_fn)
        
        # Update the self.cur_res for output results
        self.cur_res = query_imgs
        
        #############################################
        # Order Images
        #############################################
        
        json_res = query_imgs.get_raw()
        
        # Send orders to the RAPI
        order_res = self.eodms_rapi.order(json_res, priority)
        
        # Convert results to an OrderList
        orders = image.OrderList(self, query_imgs)
        orders.ingest_results(order_res)
        
        # Update the self.cur_res for output results
        self.cur_res = query_imgs
        
        if orders.count_items() == 0:
            # If no orders could be found
            self.export_results()
            err_msg = "No orders were submitted successfully."
            self.print_support(err_msg)
            self.logger.error(err_msg)
            sys.exit(1)
        
        # Get a list of order items in JSON format for the EODMSRAPI
        items = orders.get_raw()
        
        #############################################
        # Download Images
        #############################################
        
        # Make the download folder if it doesn't exist
        if not os.path.exists(self.download_path):
            os.mkdir(self.download_path)
        
        # Download images using the EODMSRAPI
        download_items = self.eodms_rapi.download(items, self.download_path)
        
        # Update images
        query_imgs.update_downloads(download_items)
        
        # Export polygons of images
        eodms_geo = geo.Geo()
        eodms_geo.export_results(query_imgs, self.output)
        
        # Update the self.cur_res for output results
        self.cur_res = query_imgs
        self.export_results()
        
        end_time = datetime.datetime.now()
        end_str = end_time.strftime("%Y-%m-%d %H:%M:%S")
        
        self.logger.info("End time: %s" % end_str)
        
    def download_aoi(self, params):
        """
        Runs a query and downloads images from existing orders.
        
        :param params: A dictionary containing the arguments and values.
        :type  params: dict
        """
        
        # Log the parameters
        self.log_parameters(params)
        
        # Get all the values from the parameters
        collections = params.get('collections')
        dates = params.get('dates')
        aoi = params.get('input')
        filters = params.get('filters')
        process = params.get('process')
        maximum = params.get('maximum')
        self.output = params.get('output')
        priority = params.get('priority')
        
        # Validate AOI
        aoi_check = self.validate_file(aoi, True)
        if not aoi_check:
            err_msg = "The provided input file is not a valid AOI " \
                        "file. Exiting process."
            self.print_support()
            self.logger.error(err_msg)
            sys.exit(1)
            
        # Create info folder, if it doesn't exist, to store CSV files
        start_time = datetime.datetime.now()
        start_str = start_time.strftime("%Y-%m-%d %H:%M:%S")
        self.fn_str = start_time.strftime("%Y%m%d_%H%M%S")
        
        self.logger.info("Process start time: %s" % start_str)
        
        #############################################
        # Search for Images
        #############################################
        
        # Parse maximum items
        _, max_items = self.parse_max(maximum)
        
        # Convert collections to list if not already
        if not isinstance(collections, list):
            collections = [collections]
            
        # Parse dates if not already done
        if not isinstance(dates, list):
            dates = self._parse_dates(dates)
            
        # Send query to EODMSRAPI
        query_imgs = self.query_entries(collections, filters=filters, \
            aoi=aoi, dates=dates)
            
        # If no results were found, inform user and end process
        if query_imgs.count() == 0:
            msg = "Sorry, no results found for given AOI."
            self.print_msg(msg)
            self.print_msg("Exiting process.")
            self.print_support()
            self.logger.warning(msg)
            sys.exit(1)
            
        # Update the self.cur_res for output results
        self.cur_res = query_imgs
        
        # Print results info
        msg = "%s images returned from search results.\n" % query_imgs.count()
        self.print_footer('Query Results', msg)
        
        #############################################
        # Get Existing Order Results
        #############################################
        
        orders = self.retrieve_orders(query_imgs)
                    
        #############################################
        # Download Images
        #############################################
                    
        # Get a list of order items in JSON format for the EODMSRAPI
        items = orders.get_raw()
        
        # Make the download folder if it doesn't exist
        if not os.path.exists(self.download_path):
            os.mkdir(self.download_path)
        
        # Download images using the EODMSRAPI
        download_items = self.eodms_rapi.download(items, self.download_path)
        
        # Update images with download info
        query_imgs.update_downloads(download_items)
        
        self._print_results(query_imgs)
        
        # Export polygons of images
        eodms_geo = geo.Geo()
        eodms_geo.export_results(query_imgs, self.output)
        
        # Update the self.cur_res for output results
        self.cur_res = query_imgs
        self.export_results()
        
        end_time = datetime.datetime.now()
        end_str = end_time.strftime("%Y-%m-%d %H:%M:%S")
        
        self.logger.info("End time: %s" % end_str)
        
    def download_only(self, params):
        """
        Downloads existing images using the CSV results file from a previous session.
        
        :param params: A dictionary containing the arguments and values.
        :type  params: dict
        """
        
        # Log the parameters
        self.log_parameters(params)
        
        csv_fn = params.get('input')
        self.output = params.get('output')
        
        if csv_fn.find('.csv') == -1:
            msg = "The provided input file is not a CSV file. " \
                "Exiting process."
            self.print_support(msg)
            self.logger.error(msg)
            sys.exit(1)
        
        # Create info folder, if it doesn't exist, to store CSV files
        start_time = datetime.datetime.now()
        start_str = start_time.strftime("%Y-%m-%d %H:%M:%S")
        self.fn_str = start_time.strftime("%Y%m%d_%H%M%S")
        folder_str = start_time.strftime("%Y-%m-%d")
        
        self.logger.info("Process start time: %s" % start_str)
        
        ################################################
        # Get results from Results CSV
        ################################################
        
        query_imgs = self._get_prevRes(csv_fn)
        
        ################################################
        # Get Existing Orders
        ################################################
        
        orders = self.retrieve_orders(query_imgs)
                
        ################################################
        # Download Images
        ################################################
                
        # Get a list of order items in JSON format for the EODMSRAPI
        items = orders.get_raw()
        
        # Make the download folder if it doesn't exist
        if not os.path.exists(self.download_path):
            os.mkdir(self.download_path)
        
        # Download images using the EODMSRAPI
        download_items = self.eodms_rapi.download(items, self.download_path)
        
        # Update images with download info
        query_imgs.update_downloads(download_items)
        
        self._print_results(query_imgs)
        
        # Export polygons of images
        eodms_geo = geo.Geo()
        eodms_geo.export_results(query_imgs, self.output)
        
        self.export_results()
        
        end_time = datetime.datetime.now()
        end_str = end_time.strftime("%Y-%m-%d %H:%M:%S")
        
        self.logger.info("End time: %s" % end_str)
        
    def search_only(self, params):
        """
        Only runs a search on the EODMSRAPI based on user parameters.
        
        :param params: A dictionary of parameters from the user.
        :type  params: dict
        """
        
        # Log the parameters
        self.log_parameters(params)
        
        # Get all the values from the parameters
        collections = params.get('collections')
        dates = params.get('dates')
        aoi = params.get('input')
        filters = params.get('filters')
        process = params.get('process')
        maximum = params.get('maximum')
        self.output = params.get('output')
        priority = params.get('priority')
        
        # Validate AOI
        aoi_check = self.validate_file(aoi, True)
        if not aoi_check:
            err_msg = "The provided input file is not a valid AOI " \
                        "file. Exiting process."
            self.print_support()
            self.logger.error(err_msg)
            sys.exit(1)
            
        # Create info folder, if it doesn't exist, to store CSV files
        start_time = datetime.datetime.now()
        start_str = start_time.strftime("%Y-%m-%d %H:%M:%S")
        self.fn_str = start_time.strftime("%Y%m%d_%H%M%S")
        
        self.logger.info("Process start time: %s" % start_str)
        
        #############################################
        # Search for Images
        #############################################
        
        # Parse maximum items
        max_images, max_items = self.parse_max(maximum)
        
        # Convert collections to list if not already
        if not isinstance(collections, list):
            collections = [collections]
            
        # Parse dates if not already done
        if not isinstance(dates, list):
            dates = self._parse_dates(dates)
            
        # Send query to EODMSRAPI
        query_imgs = self.query_entries(collections, filters=filters, \
            aoi=aoi, dates=dates, max_images=max_images)
            
        # If no results were found, inform user and end process
        if query_imgs.count() == 0:
            msg = "Sorry, no results found for given AOI."
            self.print_msg(msg)
            self.print_msg("Exiting process.")
            self.print_support()
            self.logger.warning(msg)
            sys.exit(1)
            
        # Update the self.cur_res for output results
        self.cur_res = query_imgs
        
        # Print results info
        msg = "%s images returned from search results.\n" % query_imgs.count()
        self.print_footer('Query Results', msg)
        
        # Export polygons of images
        eodms_geo = geo.Geo()
        eodms_geo.export_results(query_imgs, self.output)
        
        # Export results to a CSV file and end process.
        self.export_results()
        
        print("\n%s images found intersecting your AOI." % \
            query_imgs.count())
        print("\nPlease check the results folder for more info.")
        print("\nExiting process.")
        
        self.print_support()
        sys.exit(0)
        
class Prompter():
    
    """
    Class used to prompt the user for all inputs.
    """
    
    def __init__(self, eod, config_info, params):
        """
        Initializer for the Prompter class.
        
        :param eod: The Eodms_OrderDownload object.
        :type  eod: self.Eodms_OrderDownload
        :param config_info: Configuration information taken from the config file.
        :type  config_info: dict
        :param params: An empty dictionary of parameters.
        :type  params: dict
        """
        
        self.eod = eod
        self.config_info = config_info
        self.params = params
        
        self.logger = logging.getLogger('eodms')
        
        self.choices = {'full': 'Search, order & download images using ' \
                    'an AOI', \
                'order_csv': 'Order & download images using EODMS UI ' \
                    'search results (CSV file)', 
                'download_only': '''Download existing orders using a CSV file 
        from a previous order/download process (files found under "results" 
        folder)''', 
                'search_only': 'Run only a search based on an AOI '\
                    'and input parameters'}

    def ask_aoi(self, input_fn):
        """
        Asks the user for the geospatial input filename.
        
        :param input_fn: The geospatial input filename if already set by the command-line.
        :type  input_fn: str
        
        :return: The geospatial filename entered by the user.
        :rtype: str
        """
        
        if input_fn is None or input_fn == '':
                    
            if self.eod.silent:
                err_msg = "No AOI file specified. Exiting process."
                self.eod.print_support(err_msg)
                self.logger.error(err_msg)
                sys.exit(1)
            
            msg = "\nEnter the full path name of a GML, KML, Shapefile or " \
                    "GeoJSON containing an AOI to restrict the search " \
                    "to a specific location"
            err_msg = "No AOI specified. Please enter a valid GML, KML, " \
                    "Shapefile or GeoJSON file"
            input_fn = self.get_input(msg, err_msg)
            
        if input_fn.find('.shp') > -1:
            try:
                import ogr
                import osr
                GDAL_INCLUDED = True
            except ImportError:
                try:
                    import osgeo.ogr as ogr
                    import osgeo.osr as osr
                    GDAL_INCLUDED = True
                except ImportError:
                    err_msg = "Cannot open a Shapefile without GDAL. Please install " \
                        "the GDAL Python package if you'd like to use a Shapefile " \
                        "for your AOI."
                    self.eod.print_support(err_msg)
                    self.logger.error(err_msg)
                    sys.exit(1)
                    
        input_fn = input_fn.strip()
        input_fn = input_fn.strip("'")
        input_fn = input_fn.strip('"')
        
        #---------------------------------
        # Check validity of the input file
        #---------------------------------
        
        input_fn = self.eod.validate_file(input_fn, True)
        
        if not input_fn:
            sys.exit(1)
            
        return input_fn
        
    def ask_collection(self, coll, coll_lst):
        """
        Asks the user for the collection(s).
        
        :param coll: The collections if already set by the command-line.
        :type  coll: str
        :param coll_lst: A list of collections retrieved from the RAPI.
        :type  coll_lst: list
        
        :return: A list of collections entered by the user.
        :rtype: list
        """
        
        if coll is None:
                    
            if self.eod.silent:
                err_msg = "No collection specified. Exiting process."
                self.eod.print_support(err_msg)
                self.logger.error(err_msg)
                sys.exit(1)
            
            # List available collections for this user
            print("\nAvailable Collections:")
            for idx, c in enumerate(coll_lst):
                if c == 'National Air Photo Library':
                    c += ' (open data only)'
                print("%s. %s" % (idx + 1, c))
            
            # Prompted user for number(s) from list
            msg = "Enter the number of a collection from the list " \
                    "above (for multiple collections, enter each number " \
                    "separated with a comma)"
            err_msg = "At least one collection must be specified."
            in_coll = self.get_input(msg, err_msg)
            
            # Convert number(s) to collection name(s)
            coll_vals = in_coll.split(',')
            
            #---------------------------------------
            # Check validity of the collection entry
            #---------------------------------------
            
            check = self.eod.validate_int(coll_vals, len(coll_lst))
            if not check:
                err_msg = "A valid Collection must be specified. " \
                            "Exiting process."
                self.eod.print_support(err_msg)
                self.logger.error(err_msg)
                sys.exit(1)
            
            coll = [coll_lst[int(i) - 1] for i in coll_vals if i.isdigit()]
        else:
            coll = coll.split(',')
            
        #------------------------------
        # Check validity of Collections
        #------------------------------
        for c in coll:
            check = self.eod.validate_collection(c)
            if not check:
                err_msg = "Collection '%s' is not valid." % c
                self.eod.print_support(err_msg)
                self.logger.error(err_msg)
                sys.exit(1)
                
        return coll
        
    def ask_dates(self, dates):
        """
        Asks the user for dates.
        
        :param dates: The dates if already set by the command-line.
        :type  dates: str
        
        :return: The dates entered by the user.
        :rtype: str
        """
        
        # Get the date range
        if dates is None:
            
            if not self.eod.silent:
                msg = "\nEnter a date range (ex: 20200525-20200630) " \
                        "or a previous time-frame (24 hours) " \
                        "(leave blank to search all years)"
                dates = self.get_input(msg, required=False)
                
        #-------------------------------
        # Check validity of filter input
        #-------------------------------
        if dates is not None and not dates == '':
            dates = self.eod.validate_dates(dates)
            
            if not dates:
                err_msg = "The dates entered are invalid. "
                self.eod.print_support(err_msg)
                self.logger.error(err_msg)
                sys.exit(1)
                
        return dates
                
    def ask_filter(self, filters):
        """
        Asks the user for the search filters.
        
        :param filters: The filters if already set by the command-line.
        :type  filters: str
        
        :return: A dictionary containing the filters entered by the user.
        :rtype: dict
        """
        
        if filters is None:
            filt_dict = {}
            
            if not self.eod.silent:
                # Ask for the filters for the given collection(s)
                for coll in self.params['collections']:
                    coll_id = self.eod.get_collIdByName(coll)
                    
                    if coll_id in self.eod.get_filtMap().keys():
                        print("\nAvailable filters for '%s':" % coll)
                        for c in self.eod.get_filtMap()[coll_id].keys():
                            print("  %s" % c)
                        msg = "Enter the filters you would like to apply " \
                                "to the query (in the format of " \
                                "<filter_id>=<value>|<value>|...; " \
                                "separate each filter property with a comma)"
                        
                        filt_items = input("%s:\n" % msg)
                        
                        #filt_items = self.get_input(msg, required=False)
                        
                        if filt_items == '':
                            filt_dict[coll_id] = []
                        else:
                            
                            #-------------------------------
                            # Check validity of filter input
                            #-------------------------------
                            filt_items = self.eod.validate_filters(filt_items, \
                                            coll_id)
                            
                            if not filt_items:
                                sys.exit(1)
                            
                            filt_items = filt_items.split(',')
                            # In case the user put collections in filters
                            filt_items = [f.split('.')[1] \
                                if f.find('.') > -1 \
                                else f for f in filt_items]
                            filt_dict[coll_id] = filt_items
                            
        else:
            # User specified in command-line
            
            # Possible formats:
            #   1. Only one collection: <field_id>=<value>|<value>,<field_id>=<value>&<value>,...
            #   2. Multiple collections but only specifying one set of filters:
            #       <coll_id>.<filter_id>=<value>|<value>,...
            #   3. Multiple collections with filters:
            #       <coll_id>.<filter_id>=<value>,...<coll_id>.<filter_id>=<value>,...
            
            filt_dict = {}
            
            for coll in self.params['collections']:
                # Split filters by comma
                filt_lst = filters.split(',')
                for f in filt_lst:
                    if f == '': continue
                    if f.find('.') > -1:
                        coll_id, filt_items = f.split('.')
                        filt_items = self.eod.validate_filters(filt_items, coll_id)
                        if not filt_items:
                            sys.exit(1)
                        if coll_id in filt_dict.keys():
                            coll_filters = filt_dict[coll_id]
                        else:
                            coll_filters = []
                        coll_filters.append(filt_items.replace('"', '').\
                            replace("'", ''))
                        filt_dict[coll_id] = coll_filters
                    else:
                        coll_id = self.eod.get_collIdByName(coll)
                        if coll_id in filt_dict.keys():
                            coll_filters = filt_dict[coll_id]
                        else:
                            coll_filters = []
                        coll_filters.append(f)
                        filt_dict[coll_id] = coll_filters
                    
        return filt_dict
        
    def ask_inputFile(self, input_fn, msg):
        """
        Asks the user for the input filename.
        
        :param input_fn: The input filename if already set by the command-line.
        :type  input_fn: str
        :param msg: The message used to ask the user.
        :type  msg: str
        
        :return: The input filename.
        :rtype: str
        """
        
        if input_fn is None or input_fn == '':
            
            if self.eod.silent:
                err_msg = "No CSV file specified. Exiting process."
                self.eod.print_support(err_msg)
                self.logger.error(err_msg)
                sys.exit(1)
            
            err_msg = "No CSV specified. Please enter a valid CSV file"
            input_fn = self.get_input(msg, err_msg)
            
        if not os.path.exists(input_fn):
            err_msg = "Not a valid CSV file. Please enter a valid CSV file."
            self.eod.print_support(err_msg)
            self.logger.error(err_msg)
            sys.exit(1)
            
        return input_fn
        
    def ask_maximum(self, maximum):
        """
        Asks the user for maximum number of order items and the number of items per order.
        
        :param maximum: The maximum if already set by the command-line.
        :type  maximum: str
        
        :return: The maximum number of order items and/or number of items per order, separated by ':'.
        :rtype: str
        """
        
        if maximum is None or maximum == '':
                        
            if not self.eod.silent:
                if not self.process == 'order_csv':
                    msg = "\nEnter the total number of images you'd " \
                        "like to order (leave blank for no limit)"
                    
                    total_records = self.get_input(msg, required=False)
                    
                    #------------------------------------------
                    # Check validity of the total_records entry
                    #------------------------------------------
                
                    if total_records == '':
                        total_records = None
                    else:
                        total_records = self.eod.validate_int(total_records)
                        if not total_records:
                            self.eod.print_msg("WARNING: Total number of images " \
                                "value not valid. Excluding it.", indent=False)
                            total_records = None
                        else:
                            total_records = str(total_records)
                else:
                    total_records = None
                
                msg = "\nIf you'd like a limit of images per order, " \
                    "enter a value (EODMS sets a maximum limit of 100)"
            
                order_limit = self.get_input(msg, required=False)
                
                if order_limit == '':
                    order_limit = None
                else:
                    order_limit = self.eod.validate_int(order_limit, 100)
                    if not order_limit:
                        self.eod.print_msg("WARNING: Order limit value not " \
                            "valid. Excluding it.", indent=False)
                        order_limit = None
                    else:
                        order_limit = str(order_limit)
                
                maximum = ':'.join(filter(None, [total_records, \
                            order_limit]))
                            
        else:
            
            if self.process == 'order_csv':
                
                if maximum.find(':') > -1:
                    total_records, order_limit = maximum.split(':')
                else:
                    total_records = None
                    order_limit = maximum
                    
                maximum = ':'.join(filter(None, [total_records, \
                                order_limit]))
                            
        return maximum
        
    def ask_output(self, output):
        """
        Asks the user for the output geospatial file.
        
        :param output: The output if already set by the command-line.
        :type  output: str
        
        :return: The output geospatial filename.
        :rtype: str
        """
        
        if output is None:
                    
            if not self.eod.silent:
                msg = "\nEnter the path of the output geospatial file " \
                    "(can also be GeoJSON, KML, GML or Shapefile) " \
                    "(default is no output file)"
                output = self.get_input(msg, required=False)
                
        return output
        
    def ask_priority(self, priority):
        """
        Asks the user for the order priority level
        
        :param priority: The priority if already set by the command-line.
        :type  priority: str
        
        :return: The priority level.
        :rtype: str
        """
        
        priorities = ['low', 'medium', 'high', 'urgent']
        
        if priority is None:
            if not self.eod.silent:
                msg = "\nEnter the priority level for the order ('Low', " \
                        "'Medium', 'High', 'Urgent') [Medium]"
                        
                priority = self.get_input(msg, required=False)
            
        if priority is None or priority == '':
            priority = 'Medium'
        elif priority.lower() not in priorities:
            self.eod.print_msg("WARNING: Not a valid 'priority' entry. " \
                "Setting priority to 'Medium'.", indent=False)
            priority = 'Medium'

    def ask_process(self):
        """
        Asks the user what process they would like to run.
        
        :return: The value the process the user has chosen.
        :rtype: str
        """
        
        if self.eod.silent:
            process = 'full'
        else:
            process = input("\nWhat would you like to do?\n%s\n" \
                    "Please choose the type of process [1]: " % \
                    '\n'.join(["%s: (%s) %s" % (idx + 1, v[0], \
                        re.sub(r'\s+', ' ', v[1].replace('\n', ''))) \
                        for idx, v in enumerate(self.choices.items())]))
                    
            if process == '':
                process = 'full'
            else:
                # Set process value and check its validity
                
                process = self.eod.validate_int(process)
                
                if not process:
                    err_msg = "Invalid value entered for the 'process' " \
                                "parameter."
                    self.eod.print_support(err_msg)
                    self.logger.error(err_msg)
                    sys.exit(1)
                
                if process > len(self.choices.keys()):
                    err_msg = "Invalid value entered for the 'process' " \
                                "parameter."
                    self.eod.print_support(err_msg)
                    self.logger.error(err_msg)
                    sys.exit(1)
                else:
                    process = list(self.choices.keys())[int(process) - 1]
                    
        return process

    def build_syntax(self):
        """
        Builds the command-line syntax to print to the command prompt.
        
        :return: A string containing the command-line syntax for the script.
        :rtype: str
        """
        
        # Get the actions of the argparse
        actions = self.parser._option_string_actions
        
        syntax_params = []
        for p, pv in self.params.items():
            if pv is None or pv == '': continue
            if p == 'session': continue
            if p == 'eodms_rapi': continue
            action = actions['--%s' % p]
            flag = action.option_strings[0]
            
            if isinstance(pv, list):
                if flag == '-d':
                    pv = '-'.join(['"%s"' % i if i.find(' ') > -1 else i \
                            for i in pv ])
                else:
                    pv = ','.join(['"%s"' % i if i.find(' ') > -1 else i \
                            for i in pv ])
                            
            elif isinstance(pv, dict):
                
                if flag == '-f':
                    filt_lst = []
                    for k, v_lst in pv.items():
                        for v in v_lst:
                            if v is None or v == '': continue
                            v = v.replace('"', '').replace("'", '')
                            filt_lst.append("%s.%s" % (k, v))
                    if len(filt_lst) == 0: continue
                    pv = '"%s"' % ','.join(filt_lst)
            else:
                if isinstance(pv, str) and pv.find(' ') > -1:
                    pv = '"%s"' % pv
            
            syntax_params.append('%s %s' % (flag, pv))
            
        out_syntax = "python %s %s -s" % (os.path.realpath(__file__), \
                        ' '.join(syntax_params))
        
        return out_syntax
        
    def get_input(self, msg, err_msg=None, required=True, password=False):
        """
        Gets an input from the user for an argument.
        
        :param msg: The message used to prompt the user.
        :type  msg: str
        :param err_msg: The message to print when the user enters an invalid input.
        :type  err_msg: str
        :param required: Determines if the argument is required.
        :type  required: boolean
        :param password: Determines if the argument is for password entry.
        :type  password: boolean
        
        :return: The value entered by the user.
        :rtype: str
        """
        
        if password:
            # If the argument is for password entry, hide entry
            in_val = getpass.getpass(prompt='%s: ' % msg)
        else:
            in_val = input("%s: " % msg)
            
        if required and in_val == '':
            Eodms_OrderDownload().print_support(err_msg)
            self.logger.error(err_msg)
            sys.exit(1)
            
        return in_val
        
    def print_syntax(self):
        """
        Prints the command-line syntax for the script.
        """
        
        print("\nUse this command-line syntax to run the same parameters:")
        cli_syntax = self.build_syntax()
        print(cli_syntax)
        self.logger.info("Command-line Syntax: %s" % cli_syntax)
        
    def prompt(self):
        """
        Prompts the user for the input options.
        """
        
        self.parser = argparse.ArgumentParser(description='Search & Order EODMS ' \
                            'products.', \
                            formatter_class=argparse.RawTextHelpFormatter)
        
        self.parser.add_argument('-u', '--username', help='The username of the ' \
                            'EODMS account used for authentication.')
        self.parser.add_argument('-p', '--password', help='The password of the ' \
                            'EODMS account used for authentication.')
        input_help = '''An input file, can either be an AOI or a CSV file 
    exported from the EODMS UI. Valid AOI formats are GeoJSON, 
    KML or Shapefile (Shapefile requires the GDAL Python package).'''
        self.parser.add_argument('-i', '--input', help=input_help)
        coll_help = '''The collection of the images being ordered 
    (separate multiple collections with a comma).'''
        self.parser.add_argument('-c', '--collections', help=coll_help)
        self.parser.add_argument('-f', '--filters', help='A list of filters for ' \
                            'a specific collection.')
        self.parser.add_argument('-l', '--priority', help='The priority level of '\
                            'the order.\nOne of "Low", "Medium", "High" or ' \
                            '"Urgent" (default "Medium").')
        self.parser.add_argument('-d', '--dates', help='The date ranges for the ' \
                            'search.')
        max_help = '''The maximum number of images to order and download 
    and the maximum number of images per order, separated by a colon.'''
        self.parser.add_argument('-m', '--maximum', help=max_help)
        self.parser.add_argument('-r', '--process', help='The type of process to run ' \
                            'from this list of options:\n- %s' % \
                            '\n- '.join(["%s: %s" % (k, v) for k, v in \
                            self.choices.items()]))
        output_help = '''The output file path containing the results in a geospatial format.
The output parameter can be:
- None (empty): No output will be created (a results CSV file will still be 
    created in the 'results' folder)
- GeoJSON: The output will be in the GeoJSON format 
    (use extension .geojson or .json)
- KML: The output will be in KML format (use extension .kml) (requires GDAL Python package) 
- GML: The output will be in GML format (use extension .gml) (requires GDAL Python package) 
- Shapefile: The output will be ESRI Shapefile (requires GDAL Python package) 
    (use extension .shp)'''
        self.parser.add_argument('-o', '--output', help=output_help)
        self.parser.add_argument('-s', '--silent', action='store_true', \
                            help='Sets process to silent ' \
                            'which supresses all questions.')
        
        args = self.parser.parse_args()
        
        user = args.username
        password = args.password
        coll = args.collections
        dates = args.dates
        input_fn = args.input
        filters = args.filters
        priority = args.priority
        maximum = args.maximum
        process = args.process
        output = args.output
        silent = args.silent
        
        self.eod.set_silence(silent)
        
        print("\n##########################################################" \
                "#######################")
        print("# EODMS API Orderer & Downloader                            " \
                "                    #")
        print("############################################################" \
                "#####################")
                
        new_user = False
        new_pass = False
        
        if user is None:
            
            user = self.config_info.get('RAPI', 'username')
            if user == '':
                msg = "\nEnter the username for authentication"
                err_msg = "A username is required to order images."
                user = self.get_input(msg, err_msg)
                new_user = True
            else:
                print("\nUsing the username set in the 'config.ini' file...")
                
        if password is None:
            
            password = self.config_info.get('RAPI', 'password')
            
            if password == '':
                msg = 'Enter the password for authentication'
                err_msg = "A password is required to order images."
                password = self.get_input(msg, err_msg, password=True)
                new_pass = True
            else:
                password = base64.b64decode(password).decode("utf-8")
                print("Using the password set in the 'config.ini' file...")
                
        if new_user or new_pass:
            suggestion = ''
            if self.eod.silent:
                suggestion = " (it is best to store the credentials if " \
                            "you'd like to run the script in silent mode)"
            
            answer = input("\nWould you like to store the credentials " \
                    "for a future session%s? (y/n):" % suggestion)
            if answer.lower().find('y') > -1:
                self.config_info.set('RAPI', 'username', user)
                pass_enc = base64.b64encode(password.encode("utf-8")).decode("utf-8")
                self.config_info.set('RAPI', 'password', \
                    str(pass_enc))
                
                config_fn = os.path.join(os.path.dirname(\
                            os.path.abspath(__file__)), \
                            'config.ini')
                cfgfile = open(config_fn, 'w')
                self.config_info.write(cfgfile, space_around_delimiters=False)
                cfgfile.close()
        
        # Get number of attempts when querying the RAPI
        self.eod.set_attempts(self.config_info.get('RAPI', 'access_attempts'))
        
        self.eod.create_session(user, password)
        
        self.params = {'collections': coll, 
                        'dates': dates, 
                        'input': input_fn, 
                        'maximum': maximum, 
                        'process': process}
        
        coll_lst = self.eod.eodms_rapi.get_collections(True)
        
        if coll_lst is None:
            msg = "Failed to retrieve a list of available collections."
            self.logger.error(msg)
            self.eod.print_support(msg)
            sys.exit(1)
        
        print("\n(For more information on the following prompts, please refer" \
                " to the README file.)")
        
        #########################################
        # Get the type of process
        #########################################
        
        if process is None or process == '':
            self.process = self.ask_process()
        else:
            self.process = process
                    
        self.params['process'] = self.process
        
        if self.process == 'full':
            
            self.logger.info("Searching, ordering and downloading images " \
                        "using an AOI.")
                        
            # Get the AOI file
            input_fn = self.ask_aoi(input_fn)
            self.params['input'] = input_fn
            
            # Get the collection(s)
            coll = self.ask_collection(coll, coll_lst)
            self.params['collections'] = coll
            
            # Get the filter(s)
            filt_dict = self.ask_filter(filters)
            self.params['filters'] = filt_dict
            
            # Get the date(s)
            dates = self.ask_dates(dates)
            self.params['dates'] = dates
            
            # Get the output geospatial filename
            output = self.ask_output(output)
            self.params['output'] = output
            
            # Get the maximum(s)
            maximum = self.ask_maximum(maximum)
            self.params['maximum'] = maximum
            
            # Get the priority
            priority = self.ask_priority(priority)       
            self.params['priority'] = priority
            
            # Print command-line syntax for future processes
            self.print_syntax()
            
            self.eod.search_orderDownload(self.params)
            
        elif self.process == 'order_csv':
            
            self.logger.info("Ordering and downloading images using results " \
                        "from a CSV file.")
            
            #########################################
            # Get the CSV file
            #########################################
            
            msg = "\nEnter the full path of the CSV file exported "\
                        "from the EODMS UI website"
            input_fn = self.ask_inputFile(input_fn, msg)
            self.params['input'] = input_fn
            
            # Get the output geospatial filename
            output = self.ask_output(output)
            self.params['output'] = output
            
            # Get the maximum(s)
            maximum = self.ask_maximum(maximum)
            self.params['maximum'] = maximum
            
            # Get the priority
            priority = self.ask_priority(priority)
            self.params['priority'] = priority
            
            # Print command-line syntax for future processes
            self.print_syntax()
            
            # Run the order_csv process
            self.eod.order_csv(self.params)
            
        elif self.process == 'download_aoi' or self.process == 'search_only':
            
            if self.process == 'download_aoi':
                self.logger.info("Downloading existing orders using an AOI.")
            else:
                self.logger.info("Searching for images using an AOI.")
            
            # Get the AOI file
            input_fn = self.ask_aoi(input_fn)
            self.params['input'] = input_fn
            
            # Get the collection(s)
            coll = self.ask_collection(coll, coll_lst)
            self.params['collections'] = coll
            
            # Get the filter(s)
            filt_dict = self.ask_filter(filters)
            self.params['filters'] = filt_dict
            
            # Get the date(s)
            dates = self.ask_dates(dates)
            self.params['dates'] = dates
            
            # Get the output geospatial filename
            output = self.ask_output(output)
            self.params['output'] = output
            
            # Print command-line syntax for future processes
            self.print_syntax()
            
            if self.process == 'download_aoi':
                self.eod.download_aoi(self.params)
            else:
                self.eod.search_only(self.params)
            
        elif self.process == 'download_only':
            # Download existing orders using CSV file from previous session
            
            self.logger.info("Downloading images using results from a CSV " \
                        "file from a previous session.")
            
            # Get the CSV file
            msg = "\nEnter the full path of the CSV Results file from a " \
                "previous session"
            input_fn = self.ask_inputFile(input_fn, msg)
            self.params['input'] = input_fn
            
            # Get the output geospatial filename
            output = self.ask_output(output)
            self.params['output'] = output
            
            # Print command-line syntax for future processes
            self.print_syntax()
            
            # Run the download_only process
            self.eod.download_only(self.params)
        
        else:
            self.eod.print_support("That is not a valid process type.")
            self.logger.error("An invalid parameter was entered during the prompt.")
            sys.exit(1)

def get_config():
    """
    Gets the configuration information from the config file.
    
    :return: The information extracted from the config file.
    :rtype: configparser.ConfigParser
    """
    
    config = configparser.ConfigParser()
    
    config_fn = os.path.join(os.path.dirname(os.path.abspath(__file__)), \
                'config.ini')
    
    config.read(config_fn)
    
    return config
    
def print_support(err_str=None):
    """
    Prints the 2 different support message depending if an error occurred.
    
    :param err_str: The error string to print along with support.
    :type  err_str: str
    """
    
    Eodms_OrderDownload().print_support(err_str)
        
def main():
    
    cmd_title = "EODMS Order-Downloader"
    os.system("title " + cmd_title)
    sys.stdout.write("\x1b]2;%s\x07" % cmd_title)

    # Create info folder, if it doesn't exist, to store CSV files
    start_time = datetime.datetime.now()
    start_str = start_time.strftime("%Y-%m-%d %H:%M:%S")
    
    try:
        
        params = {}
        
        # Set all the parameters from the config.ini file
        config_info = get_config()
        
        abs_path = os.path.abspath(__file__)
        download_path = config_info.get('Script', 'downloads')
        if download_path == '':
            download_path = os.path.join(os.path.dirname(abs_path), \
                                    'downloads')
        elif not os.path.isabs(download_path):
            download_path = os.path.join(os.path.dirname(abs_path), \
                                    download_path)
            
        print("\nImages will be downloaded to '%s'." % download_path)
        
        res_path = config_info.get('Script', 'results')
        if res_path == '':
            res_path = os.path.join(os.path.dirname(abs_path), \
                                    'results')
        elif not os.path.isabs(res_path):
            res_path = os.path.join(os.path.dirname(abs_path), \
                                    res_path)
            
        log_loc = config_info.get('Script', 'log')
        if log_loc == '':
            log_loc = os.path.join(os.path.dirname(abs_path), \
                                    'log', 'logger.log')
        elif not os.path.isabs(log_loc):
            log_loc = os.path.join(os.path.dirname(abs_path), \
                                    log_loc)
            
        # Setup logging
        logger = logging.getLogger('EODMSRAPI')
        # logger.setLevel(logging.INFO)
        
        formatter = logging.Formatter('%(asctime)s - %(levelname)s - ' \
                    '%(message)s', datefmt='%Y-%m-%d %I:%M:%S %p')
        
        if not os.path.exists(os.path.dirname(log_loc)):
            pathlib.Path(os.path.dirname(log_loc)).mkdir(\
                parents=True, exist_ok=True)
        
        logHandler = handlers.RotatingFileHandler(log_loc, \
                        maxBytes=500000, backupCount=2)
        logHandler.setLevel(logging.DEBUG)
        logHandler.setFormatter(formatter)
        logger.addHandler(logHandler)
        
        logger.info("Script start time: %s" % start_str)
        
        # for k,v in logging.Logger.manager.loggerDict.items()  :
            # print('+ [%s] {%s} ' % (str.ljust( k, 20)  , str(v.__class__)[8:-2]) ) 
            # if not isinstance(v, logging.PlaceHolder):
                # for h in v.handlers:
                    # print('     +++',str(h.__class__)[8:-2] )
            
        timeout_query = config_info.get('Script', 'timeout_query')
        timeout_order = config_info.get('Script', 'timeout_order')
        
        try:
            timeout_query = float(timeout_query)
        except ValueError:
            timeout_query = 60.0
            
        try:
            timeout_order = float(timeout_order)
        except ValueError:
            timeout_order = 180.0
            
        # Get the total number of results per query
        max_results = config_info.get('RAPI', 'max_results')
        
        eod = Eodms_OrderDownload(download=download_path, 
                                results=res_path, log=log_loc, 
                                timeout_query=timeout_query, 
                                timeout_order=timeout_order, 
                                max_res=max_results)
            
        print("\nCSV Results will be placed in '%s'." % eod.results_path)
        
        #########################################
        # Get authentication if not specified
        #########################################
        
        prmpt = Prompter(eod, config_info, params)
        
        prmpt.prompt()
            
        print("\nProcess complete.")
        
        eod.print_support()
    
    except KeyboardInterrupt as err:
        msg = "Process ended by user."
        print("\n%s" % msg)
        
        if 'eod' in vars() or 'eod' in globals():
            eod.print_support()
            eod.export_results()
        else:
            Eodms_OrderDownload().print_support()
        logger.info(msg)
        sys.exit(1)
    except Exception:
        trc_back = "\n%s" % traceback.format_exc()
        if 'eod' in vars() or 'eod' in globals():
            eod.print_support(trc_back)
            eod.export_results()
        else:
            Eodms_OrderDownload().print_support(trc_back)
        logger.error(traceback.format_exc())

if __name__ == '__main__':
	sys.exit(main())