#
# Most code here is copyright (c) 2010 Plex Development Team. All rights reserved.
#

import re, os, os.path, random, urllib2
import Media, VideoFiles, Stack, Utils
from pprint import pprint

regex_all_in_file_name = [
    '(?P<show>.*?)[^0-9a-zA-Z]+(?P<year>[0-9]{4})[^0-9a-zA-Z]+(?P<month>[0-9]{2})[^0-9a-zA-Z]+(?P<day>[0-9]{2})['
    '^0-9a-zA-Z]+(?P<title>.*)$',
    '^(?P<show>.*?)-(?P<season>[0-9]{4}).*-([0-9a-zA-z]+-)(?P<year>[0-9]{4})(?P<month>[0-9]{2})(?P<day>[0-9]{2})'
    '[-_](?P<title>.*?)(_ALT)?$'
]

regex_date_title_file_name = [
    '.*'
]

regex_title_file_name = [
    '.*'
]

# Look for episodes.
def Scan(path, files, mediaList, subdirs):
    print "SS: Starting scan"
    #print "SS: path |", path, "|"
    #print "SS: files |", files, "|"
    #print "SS: subdirs |", subdirs, "|"

    # Scan for video files.
    VideoFiles.Scan(path, files, mediaList, subdirs)

    # print "SS: files |", files, "|"
    # print "SS: subdirs |", subdirs, "|"

    # Here we have only video files in files, path is only the TLD, media is empty, subdirs is populated
    # No files here? Then what are we doing!
    clean_files = dict()
    if len(files) == 0:
        return
    else:
        for i in files:
            file = os.path.basename(i)
            (file, ext) = os.path.splitext(file)
            # Minor cleaning on the file to avoid false matches on H.264, 720p, etc.
            whackRx = ['([hHx][\.]?264)[^0-9].*', '[^[0-9](720[pP]).*', '[^[0-9](1080[pP]).*', '[^[0-9](480[pP]).*',
                       '[^[0-9](540[pP]).*']
            for rx in whackRx:
                file = re.sub(rx, "", file)
            clean_files[file] = i

    paths = Utils.SplitPath(path)

    if len(paths) == 1 and len(paths[0]) == 0 or len(path) == 0 :
        # This is just a load of files dumped in the root directory - we can't deal with this properly
        print "SS: In TLD, no files here can be scanned"
        return

    elif len(paths) == 1 and len(paths[0]) > 0:
        # These files have been dumped into a League directory but have no seasons.
        for file in clean_files:
            print "SS: Working on file | {0} |".format(file)

            # jump in here for some additional metadata logic
            additional_metadata_file = os.path.splitext(clean_files[file])[0] + '.SportScanner'
            additional_metadata_subepisode = '';
            if os.path.isfile(additional_metadata_file):
                additional_metadata_size = os.path.getsize(additional_metadata_file)
                additional_metadata_fd = os.open(additional_metadata_file, os.O_RDONLY)
                additional_metadata_lines = os.read(additional_metadata_fd, additional_metadata_size).splitlines()
                os.close(additional_metadata_fd)
                if len(additional_metadata_lines) > 0:
                    additional_metadata_subepisode = additional_metadata_lines[0]
            try:
                additional_metadata_subepisode = int(additional_metadata_subepisode)
            except ValueError:
                additional_metadata_subepisode = -1

            for rx in regex_all_in_file_name:
                match = re.search(rx, file, re.IGNORECASE)
                if match:
                    print "SS: matched regex | {0} |".format(rx)
                    year = match.group('year')
                    month = int(match.group('month'))
                    day = int(match.group('day'))
                    show = re.sub('[^0-9a-zA-Z]+', ' ', match.group('show'))
                    title = re.sub('[^0-9a-zA-Z]+', ' ', match.group('title'))
                    if 'season' in match.groups():
                        season = match.group('season')
                    else:
                        season = year

                    # Work out where the .SportScanner file should be
                    filename = re.sub(r'(.*\\).*?$',r'\1SportScanner.txt',clean_files[file])
                    print "SS: FileName: {0}".format(filename)

                    # Check to see if a .SportScanner file exists, then read in the contents
                    if os.path.isfile(filename):
                        size = os.path.getsize(filename)
                        fd = os.open(filename, os.O_RDONLY)
                        file_contents = os.read(fd, size)
                        # print "SS: FileContents: {0}".format(file_contents)
                        season_match = re.search('(?P<season>XX..)',file_contents, re.IGNORECASE)
                        if season_match:
                            season_format = season_match.group('season').lower()
                            print "SS: Using {0} season format for {1}".format(season_format, show)

                            if season_format == "xxyy":
                               # If this is a split season then get the dates
                                split_dates_match = re.search(r'(?P<month>\d{1,2}),(?P<day>\d{1,2})', file_contents, re.IGNORECASE)
                                if split_dates_match:
                                    split_month = int(split_dates_match.group('month'))
                                    split_day = int(split_dates_match.group('day'))
                                    print "SS: Split date is {0}-{1}".format(split_month, split_day)
                                    print "SS: Event date is {0}-{1}".format(month, day)
                                    if month < split_month or (month == split_month and day < split_day):
                                        print "SS: Event happened before split date"
                                        short_year = year[-2:]
                                        year_before = str(int(short_year) - 1)
                                        season = int("{0}{1}".format(year_before, short_year))
                                    else:
                                        print "SS: Event happened after split date"
                                        short_year = year[-2:]
                                        year_after = str(int(short_year) + 1)
                                        season = int("{0}{1}".format(short_year, year_after))
                                else:
                                    print "SS: Could not match dates"
                    else:
                        print "SS: Could not find {0}, defaulting to XXXX season format"

                    # Using a hash so that each file gets the same episode number on every scan
                    # The year must be included for seasons that run over a year boundary
                    if additional_metadata_subepisode < 0:
                        ep = int('%s%02d%02d%04d' % (year[-2:],month, day, abs(hash(file)) % (10 ** 4)))
                    else:
                        ep = int('%s%02d%02d%04d' % (year[-2:],month, day, additional_metadata_subepisode))
                    tv_show = Media.Episode(show, season, ep, title, int(year))
                    tv_show.released_at = '%s-%02d-%02d' % (year, month, day)
                    tv_show.parts.append(clean_files[file])
                    mediaList.append(tv_show)
                    break
                else:
                    print "SS: No match found for {0}".format(file)
    elif len(paths) >= 2:
        # Here we assume that it is in this format: League/Season
        show = paths[0]

        season = 0
        # Look for the season in obvious ways or fail
        match = re.match('Season (\d{4})', paths[1])
        if match:
            season = match.group(1)
        else:
            match = re.match('(\d{4})', paths[1])
            if match:
                season = match.group(1)

        # Look for ALL the information we need in the filename - but trust what we have already found
        for file in clean_files:
            print "SS: Working on file | {0} |".format(file)
            
            # jump in here for some additional metadata logic
            additional_metadata_file = os.path.splitext(clean_files[file])[0] + '.SportScanner'
            additional_metadata_subepisode = '';
            if os.path.isfile(additional_metadata_file):
                additional_metadata_size = os.path.getsize(additional_metadata_file)
                additional_metadata_fd = os.open(additional_metadata_file, os.O_RDONLY)
                additional_metadata_lines = os.read(additional_metadata_fd, additional_metadata_size).splitlines()
                os.close(additional_metadata_fd)
                if len(additional_metadata_lines) > 0:
                    additional_metadata_subepisode = additional_metadata_lines[0]
            try:
                additional_metadata_subepisode = int(additional_metadata_subepisode)
            except ValueError:
                additional_metadata_subepisode = -1
            
            for rx in regex_all_in_file_name:
                match = re.search(rx, file, re.IGNORECASE)
                if match:
                    print "SS: matched regex | {0} |".format(rx)
                    year = match.group('year')
                    month = int(match.group('month'))
                    day = int(match.group('day'))
                    title = re.sub('[^0-9a-zA-Z]+', ' ', match.group('title'))

                    # Using a hash so that each file gets the same episode number on every scan
                    # The year must be included for seasons that run over a year boundary
                    if additional_metadata_subepisode < 0:
                        ep = int('%s%02d%02d%04d' % (year[-2:],month, day, abs(hash(file)) % (10 ** 4)))
                    else:
                        ep = int('%s%02d%02d%04d' % (year[-2:],month, day, additional_metadata_subepisode))
                    tv_show = Media.Episode(show, season, ep, title, int(year))
                    tv_show.released_at = '%s-%02d-%02d' % (year, month, day)
                    tv_show.parts.append(clean_files[file])
                    mediaList.append(tv_show)
                    break
            # The following two loops should be used to match against other file names.
            for rx in regex_date_title_file_name:
                break
            for rx in regex_title_file_name:
                break

    # Stack the results.
    Stack.Scan(path, files, mediaList, subdirs)


import sys

if __name__ == '__main__':
    path = sys.argv[1]
    files = [os.path.join(path, file) for file in os.listdir(path)]
    media = []
    Scan(path[1:], files, media, [])
    print "SS: media |", media, "|"
