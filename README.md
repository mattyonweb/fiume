# Fiume

![alt text](docs/logo-small.png)

A toy BitTorrent client written in python, based on the official [specs](https://www.bittorrent.org/beps/bep_0003.html).

## What it can do

- Download of single-file torrents from peer!
- Save download state beetwen sessions, and start again at a later time

## What it can SOMEWHAT do

- Reach acceptable speed downloads (I may have a very low bar)

## What it can NOT do

- Download of multiple-file torrents
- Support DHT, Message Stream Encryption or any other extension 
- Manage more than one download at a time (although you could spawn more than one Fiume process to do that)
- Download from more than one peer concurrently
- Offer a decent CLI or GUI
- It can download, but I'm not so sure about uploading (correctly)
