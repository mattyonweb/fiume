# Fiume

![](https://badge.fury.io/py/Fiume.svg)

![logo](docs/logo-small.png)

A toy BitTorrent client written in python, based on the official
[specs](https://www.bittorrent.org/beps/bep_0003.html).

## Installation and usage

Install it from [pip](https://pypi.org/project/Fiume/):

``` {.example}
pip install Fiume
```

This will install a `fiume` app, accessible from command line.

`.torrents` are to be

Launch it with `fiume` from command line:

    usage: fiume [-h] [-f F] [-p PORT] [-v]
                 [--max-peer-connections MAX_PEER_CONNECTIONS]
                 [--max-concurrent-pieces MAX_CONCURRENT_PIECES] [-t TIMEOUT]
                 [--delay DELAY]

    A Bittorrent client for single-file torrent.

    optional arguments:
      -h, --help            show this help message and exit
      -f F                  path to downloading.json file, containing currently
                            downloading files (default: ~/.fiume/downloading.json)
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

## Functionalities

### What it can do

-   Download of single-file torrents from multiple peers!
-   Manage more than one .torrent at a time
-   Save download state beetwen sessions, and start again at a later
    time
-   Reach acceptable speed downloads (achives maximum download speed on
    my home connections, ie. 6MBytes/s)
-   Offer a basic CLI

### What it can NOT do

-   Download of multiple-file torrents
-   Support DHT, Message Stream Encryption or any other extension
-   While download functionalities has been tested, uploading
    functionalities are still under test (correctly)
