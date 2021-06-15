# Fiume

![alt text](docs/logo-small.png)

A toy BitTorrent client written in python, based on the official [specs](https://www.bittorrent.org/beps/bep_0003.html).

## What it can do

- Download of single-file torrents from peers!
- Save download state beetwen sessions, and start again at a later time

## What it can NOT do

- Download of multiple-file torrents
- Reach high speed downloads
- Support DHT, Message Stream Encryption or any other extension 
- Manage more than one download at a time (although you could spawn more than one Fiume process to do that)

