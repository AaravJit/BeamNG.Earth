# BeamNG.Earth on a BeamMP server

This zip turns a BeamMP server into a BeamNG.Earth server. Players join with the normal BeamMP launcher.

## Install

1. Unzip this file into your BeamMP server's folder. Its `Resources` folder merges with the server's:
   - `Resources/Client/beamng_earth.zip` is the BeamNG.Earth level and mod. BeamMP sends it to every player who joins.
   - `Resources/Server/BeamNGEarth/main.lua` is the server plugin. It keeps players who are in the same area together.
2. In `ServerConfig.toml`, set the map:

   ```toml
   Map = "/levels/beamng_earth/info.json"
   ```

3. Start (or restart) the server. The log should show `[BeamNG.Earth] plugin loaded`.

To list the server publicly you need your own BeamMP AuthKey (keymaster.beammp.com). Never share it.

## Players

- **With the BeamNG.Earth app** (recommended): start BeamNG.Earth's world generator first, then join with BeamMP.
  The world is built on your own PC.
- **Without it**: a window recommends installing it, with a link to the download page. Without the app the world
  can't load yet. Sharing the world from other players is planned.
- Use the same BeamNG.Earth version as the server's `beamng_earth.zip`.

## How players see each other

BeamNG.Earth places the world around a point (an "anchor") that each game picks and moves as you drive. The plugin
keeps players who are within about 2 miles of each other on one shared anchor, so their cars line up. Players in
other areas get their own anchor, and their cars are hidden from you until you meet. When you join, you start next
to the other players.
