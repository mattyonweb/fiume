from pathlib import Path
import logging
import bencodepy
import json
import socket
import threading

import Fiume.metainfo_decoder as md
import Fiume.state_machine as sm
import Fiume.config as config

from Fiume.utils import *
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler, FileModifiedEvent
from typing import *


class Fiume:
    """
    This is the main class for downloading multiple .torrents 
    concurrently in Fiume. Eventually, this will become the only
    way to start Fiume.

    This class reads a file `(config.IN_DOWNLOAD_FILE)`, containing
    all active .torrents (that is: not-already-completed, 
    completed-and-seeding and momentarily-paused files).

    Then, it proceedes to launch a ThreadedServer for each of these file,
    which will take care of downloading, seeding, and logging for every file.
    """
    
    def __init__(self, options):
        # Questo file contiene info in JSON su file in scaricamento
        self.downloading_file = config.IN_DOWNLOAD_FILE
        self.options = options
        self.open_connections: Dict[Path, Dict] = dict()

        self.port = self.options["port"]
        self.host = "localhost"
        
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind((self.host, self.port))


    def begin_session(self):
        """
        Starts a session: read the IN_DOWNLOAD_FILE and initiates the connections.
        """
        # Monitor for changes in downloading file (which would cause a re-parsing
        # of the downloading file)
        self.monitor_downloading_file()
        
        with open(self.downloading_file, "r") as f:
            j = json.loads(f.read())

        for item in j:
            threading.Thread(
                target = self.add_torrent,
                args = ( Path(item["torrent_path"]),
                         Path(item["output_file"]))
            ).start()

            
    def add_torrent(self, torrent_path: Path, output_file: Path):
        """
        Fires up a ThreadedServer for a single .torrent file.
        """
        local_options = self.options.copy()
            
        local_options["torrent_path"] = torrent_path
        local_options["output_file"] = output_file

        with open(local_options["torrent_path"], "rb") as f:
            metainfo = md.MetaInfo(
                bencodepy.decode(f.read()) | local_options
            )

        tm = md.TrackerManager(metainfo, local_options)
            
        t = sm.ThreadedServer(
            metainfo, tm, socket=self.sock,
            **local_options
        )

        self.open_connections[local_options["torrent_path"]] = (local_options, t.master_queue)

        main_thread_peer = threading.Thread(target=t.main)
        main_thread_peer.start()

        
    def re_parse_downloading_file(self):
        with open(self.downloading_file, "r") as f:
            try:
                j = json.loads(f.read())
            except Exception as e:
                print("ERROR: could not parse JSON file")
                print(e)
                return
            
        not_removed = set()
        for item in j:
            if Path(item["torrent_path"]) in self.open_connections:
                not_removed.add(Path(item["torrent_path"]))
                
            else:
                self.add_torrent(
                    Path(item["torrent_path"]),
                    Path(item["output_file"])
                )

        for path in set(self.open_connections) - not_removed:
            self.remove_torrent(path)

        
            
    def monitor_downloading_file(self):
        class MyHandler(FileSystemEventHandler):
            def on_modified(cls, event):
                if isinstance(event, FileModifiedEvent):
                    print("FILE DOWNLADING.json MODIFICATO")
                    self.re_parse_downloading_file()

        event_handler = MyHandler()
        observer = Observer()
        observer.schedule(event_handler, self.downloading_file, recursive=True)
        observer.start()

        # observer.stop()
        # observer.join()

    def remove_torrent(self, torrent_path: Path):
        self.open_connections[torrent_path][1].put(M_KILL())
        del self.open_connections[torrent_path]
        
