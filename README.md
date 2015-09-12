# SportScanner
Scanner and Metadata Agent for Plex that uses www.thesportsdb.com

#Installation

Plex main folder location:

    * '%LOCALAPPDATA%\Plex Media Server\'                                        # Windows Vista/7/8
    * '%USERPROFILE%\Local Settings\Application Data\Plex Media Server\'         # Windows XP, 2003, Home Server
    * '$HOME/Library/Application Support/Plex Media Server/'                     # Mac OS
    * '$PLEX_HOME/Library/Application Support/Plex Media Server/',               # Linux
    * '/var/lib/plexmediaserver/Library/Application Support/Plex Media Server/', # Debian,Fedora,CentOS,Ubuntu
    * '/usr/local/plexdata/Plex Media Server/',                                  # FreeBSD
    * '/usr/pbi/plexmediaserver-amd64/plexdata/Plex Media Server/',              # FreeNAS
    * '${JAIL_ROOT}/var/db/plexdata/Plex Media Server/',                         # FreeNAS
    * '/c/.plex/Library/Application Support/Plex Media Server/',                 # ReadyNAS
    * '/share/MD0_DATA/.qpkg/PlexMediaServer/Library/Plex Media Server/',        # QNAP
    * '/volume1/Plex/Library/Application Support/Plex Media Server/',            # Synology, Asustor
    * '/raid0/data/module/Plex/sys/Plex Media Server/',                          # Thecus
    * '/raid0/data/PLEX_CONFIG/Plex Media Server/'                               # Thecus Plex community

 - Download the latest release from https://github.com/mmmmmtasty/SportScanner/releases
 - Extract files
 - Copy the extracted directory "Scanners" into your Plex main folder location - check the list above for more clues
 - Copy the extracted directory "SportScanner.bundle" into the Plug-ins directory in your main folder location - check the list above for more clues
 - You may need to restart Plex
 - Create a new library and under Advanced options you should be able to select "SportScanner" as both your scanner and metadata agent.
 - Copy SportScanner.py into your newly created folder

#Media Format

The SportScanner scanner currently requires a very specific folder/filename setup to work properly. This is not good and is not intended to stay this way and will be extended over time to gather information from more places than just the filename and the first level folder. 

So create a library of type "TV Show" with the SportScanner scanner and metadata agent selected. In this example we will call it ~/LibraryRoot/. The next level of directories should be the name of the sport as defined by www.thesportsdb.com. Some examples are included below.

~/LibraryRoot/Soccer/
~/LibraryRoot/Ice Hockey/
~/LibraryRoot/Motorsport/

Inside this directory you can dump all files that you want to be scanned and added but there are requirement for what must be in the filename.

 - League Name
 - Date
 - Title

An example filename would be:

 - EPL.2015.08.30.Swansea-City.vs.Manchester-United.720p.HDTV.30fps.x264-Reborn4HD.mkv

Currently there is a gap between the name of a league as defined in the filename and the name of the league as defined in www.thesportsdb.com so there is a gross mapping between common mismatches in the metadata agent. This is not intended to stay.

If you stick to this format then everything should work nicely :)
