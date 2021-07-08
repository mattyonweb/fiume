# Fiume

![](https://badge.fury.io/py/Fiume.svg)

![logo](docs/logo-small.png)

A toy BitTorrent client written in python, based on the official
[specs](https://www.bittorrent.org/beps/bep_0003.html).

## Installation

Install it from [pip](https://pypi.org/project/Fiume/):

``` {.example}
pip install Fiume
```

This will install `fiume`, the actual torrent client, as well as other utilities.

### Configuration directories/files

When installed, `fiume` creates a `.fiume` directory in your home.

It will contain a file, `downloading.json`, which stores the torrents currently in download. You can modify this file freely and in any moment with your own tools, but if you want no trouble `fiume` provides a CLI command `fiume-add` to safely add new torrents.

## `fiume` usage

`fiume` is the actual program. Its CLI is as follows:

    usage: fiume [-h] [-f DOWNLOADING_JSON] [-p PORT] [-v]
                 [--max-peer-connections MAX_PEER_CONNECTIONS]
                 [--max-concurrent-pieces MAX_CONCURRENT_PIECES] [-t TIMEOUT]
                 [--delay DELAY]

    A Bittorrent client for single-file torrent.

    optional arguments:
      -h, --help            show this help message and exit
      -f DOWNLOADING_JSON   path to downloading.json file, containing currently
                            downloading files (default:
                            ~/.fiume/downloading.json)
      -p PORT, --port PORT  port for this client (default: 50146)
      -v, --verbose         increases verbosity of output (default: 0)
      --max-peer-connections MAX_PEER_CONNECTIONS
                            max num of concurrent inbound connections (default: 2)
      --max-concurrent-pieces MAX_CONCURRENT_PIECES
                            max num of concurrent pieces downloaded from/to peer
                            (default: 5)
      -t TIMEOUT, --timeout TIMEOUT
                            timeout for various components of the program (only
                            debug) (default: 10)
      --delay DELAY         delay for every sent message (only debug) (default: 0)

## `fiume-add` usage

To add/remove `.torrents` files to download, use the `fiume-add` interface:

    usage: fiume-add [-h] [-f DOWNLOADING_JSON] torrent_path output_path

    Adds a torrent to Fiume dowloading.json.

    positional arguments:
      torrent_path         path to .torrent file
      output_path          where to put downloaded files

    optional arguments:
      -h, --help           show this help message and exit
      -f DOWNLOADING_JSON  path to custom downloading.json file (default:
                           ~/.fiume/downloading.json)

## Functionalities

### What it can do

-   Download of single-file torrents!
-   Can manage more than one .torrent download at a time
-   Save download state beetwen sessions, and start again at a later time
-   Reach acceptable speed downloads (achives maximum download speed on my
    home connections, ie. 6MBytes/s)
-   Offer a basic CLI

### What it can NOT do

-   Download of multiple-file torrents
-   Support DHT, Message Stream Encryption or any other extension
-   While download functionalities has been tested, uploading
    functionalities are still under test (correctly)
