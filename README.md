# Fiume

[![PyPI version](https://badge.fury.io/py/Fiume.svg)](https://badge.fury.io/py/Fiume)

![logo](docs/logo-small.png)

A toy BitTorrent client written in python, based on the official [specs](https://www.bittorrent.org/beps/bep_0003.html).

## Installation and usage

Install it from [pip](https://pypi.org/project/Fiume/):

	pip install Fiume
	
Launch it with `fiume` from command line:

	usage: fiume [-h] [-p PORT] [-v] [--delay DELAY]
             torrent_path output_file

	positional arguments:
	torrent_path          path to .torrent file
	output_file           where to download the file

	optional arguments:
	-h, --help            show this help message and exit
	-p PORT, --port PORT  port for this client
	-v, --verbosity       debug level
	--delay DELAY         delay for every sent message (only debug)

## Functionalities	

### What it can do

- Download of single-file torrents from multiple peers!
- Save download state beetwen sessions, and start again at a later time
- Reach acceptable speed downloads (achives maximum download speed on my home connections, ie. 6MBytes/s)

### What it can SOMEWHAT do

- Offer a basic CLI

### What it can NOT do

- Download of multiple-file torrents
- Support DHT, Message Stream Encryption or any other extension 
- Manage more than one .torrent at a time (although you could spawn more than one Fiume process to do that)
- While download functionalities has been tested, uploading functionalities are still under test (correctly)
