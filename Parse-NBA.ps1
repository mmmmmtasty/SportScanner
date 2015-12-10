Param(
    #[Parameter(Mandatory = $true)]
    [datetime]$StartDate = "2015-11-20",
    [datetime]$EndDate = (Get-Date).AddDays(-1).Date
)

#Get all the teams in the league according to thesportsdb
$thesportsdbTeams = @{}

$teamUrl = "http://www.thesportsdb.com/api/v1/json/8123456712556/lookup_all_teams.php?id=4387"
$teams = (Invoke-WebRequest $teamUrl | ConvertFrom-Json).teams



#Make a hash table that we can use here to map the results of our original search to thesportsdb values
foreach ( $team in $teams ) {
    try {
        $thesportsdbTeams.Add( $team.strTeamShort, $team )
    } catch {
        Write-Error "No short team name for $($team.strTeam)"
    }
}

#Work out all the dates that we need to query for
$dates = @($StartDate.Date)
while ( $StartDate -ne $EndDate -and $EndDate ) {
    $StartDate = $StartDate.AddDays(1)
    $dates += $StartDate.Date
}

$events = @()
$LeagueID = '00'

#Get all the events that happened on this day for league 00
foreach ( $date in $dates) {
    $properDate = "$($date.Year)-$($date.Month.ToString("00"))-$($date.Day.ToString("00"))"
    $schedUrl = "http://stats.nba.com/stats/scoreboard/?GameDate=$properDate&LeagueID=$LeagueID&DayOffset=0" 
    $output = ((Invoke-WebRequest $schedUrl).Content | ConvertFrom-Json).resultSets
    $games = ($output | where { $_.Name -eq 'GameHeader'}).rowSet
    $scores = ($output | where { $_.Name -eq 'LineScore'}).rowSet
    if ( !($games -and $scores) ) {
        continue
    }

    foreach ($game in $games) {
        #Create an object for this event
        $homeTeamShort = $scores | where { $_[3] -eq $game[6]} | %{ $_[4] }
        $awayTeamShort = $scores | where { $_[3] -eq $game[7]} | %{ $_[4] }
        $homeTeam = $scores | where { $_[3] -eq $game[6]} | %{ $thesportsdbTeams.($_[4]).strTeam } 
        #if ( !$homeTeam ) {
        #    Write-Host "No name for $homeTeamShort"
        #}
        $awayTeam = $scores | where { $_[3] -eq $game[7]} | %{ $thesportsdbTeams.($_[4]).strTeam }
        #if ( !$awayTeam ) {
        #    Write-Host "No name for $awayTeamShort"
        #}
        $homeScore = $scores | where { $_[3] -eq $game[6]} | %{ $_[21] } 
        $awayScore = $scores | where { $_[3] -eq $game[7]} | %{ $_[21] } 
        $events += New-Object -TypeName psobject -Property @{
            idEvent = $null
            idSoccerXML = $null
            strEvent = "$homeTeam vs $awayTeam"
            strFilename = "NBA $properDate $homeTeam vs $awayTeam"
            strSport = "Basketball"
            idLeague = "4387"
            strLeague = "NBA"
            strSeason = "1516"
            strDescriptionEN = $null
            strHomeTeam = "$homeTeam"
            strAwayTeam = "$awayTeam"
            intHomeScore = "$homeScore"
            intRound = "0"
            intAwayScore = "$awayScore"
            intSpectators = $null
            strHomeGoalDetails = $null
            strHomeRedCards = $null
            strHomeYellowCards = $null
            strHomeLineupGoalkeeper = $null
            strHomeLineupDefense = $null
            strHomeLineupMidfield = $null
            strHomeLineupForward = $null
            strHomeLineupSubstitutes = $null
            strHomeFormation = $null
            strAwayRedCards = $null
            strAwayYellowCards = $null
            strAwayGoalDetails = $null
            strAwayLineupGoalkeeper = $null
            strAwayLineupDefense = $null
            strAwayLineupMidfield = $null
            strAwayLineupForward = $null
            strAwayLineupSubstitutes = $null
            strAwayFormation = $null
            intHomeShots = $null
            intAwayShots = $null
            dateEvent = "$properDate"
            strDate = $null
            strTime = $null
            strTVStation = $null
            idHomeTeam = $($thesportsdbTeams.($homeTeamShort).idTeam)
            idAwayTeam = $($thesportsdbTeams.($awayTeamShort).idTeam)
            strResult = $null
            strRaceCircuit = $null
            strRaceCountry = $null
            strRaceLocality = $null
            strPoster = $null
            strFanart = $null
            strThumb = $null
            strBanner = $null
            strMap = $null
            strLocked = "unlocked"
        }
    }
} 

$events | ConvertTo-Csv -NoTypeInformation | out-file -Encoding ascii -FilePath "C:\temp\NBAresults.csv" -Force


