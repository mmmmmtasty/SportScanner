# TODO: Add the ability to pass parameters to test script

api_token=<insert API token here>
library_id=11
log_file="/config/Library/Application Support/Plex Media Server/Logs/PMS Plugin Logs/com.plexapp.agents.sportscanner.log"

rm -f $log_file

# Copy latest versions of files
echo 'Updating Scanner'
cp -rf /code/SportScanner/Scanners/Series /config/Library/Application\ Support/Plex\ Media\ Server/Scanners/
echo 'Updating Metadata Agent'
cp -rf /code/SportScanner/SportScanner.bundle /config/Library/Application\ Support/Plex\ Media\ Server/Plug-ins/

sleep 1

# Run content scan and metadata refresh
scan_url="http://localhost:32400/library/sections/${library_id}/refresh?force=1&X-Plex-Token=${api_token}"
echo "Calling ${scan_url}"
curl $scan_url

sleep 3

# Tail log file to view progress of test run
tail -f -n 50 "${log_file}"