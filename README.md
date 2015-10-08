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

##Flat folder structure

All files can be placed directly into the folder you created in the first step. All information required to match the file should be in the file name. 

Inside this directory you can dump all files that you want to be scanned and added but there are requirement for what must be in the filename.

 - League Name
 - Date
 - Title

An example filename would be:

 - EPL.2015.08.30.Swansea-City.vs.Manchester-United.720p.HDTV.30fps.x264-Reborn4HD.mkv

Currently there is a gap between the name of a league as defined in the filename and the name of the league as defined in www.thesportsdb.com so there is a mapping between common mismatches in the metadata agent. This is not intended to stay.

##Information in folder structure

You can alternatively provide information in the folder structure. You can then split by Sport, League and Season. For example for 2015/2016 NHL you would do something like the following:

 - ~LibraryRoot/Ice Hockey/NHL/Season 1516/NHL.2015.09.25.New-York-Islanders.vs.Philadelphia-Flyers.720p.HDTV.60fps.x264-Reborn4HD_h.mp4

In this scenario you still need all the information in the file name, I aim to remove that requirement down the line. The only information that comes only from the folder structure is the season. 

#Known Issues
 - No posters for seasons or individual event thumbs
 - Can only handle individual files, not multipart or those in folders
 - All relevant information needs to be in the filename even if it is already outlined in the folder structure for now
