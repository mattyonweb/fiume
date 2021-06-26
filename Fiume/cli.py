import argparse
import pathlib
import logging
import bencodepy
import signal

import Fiume.metainfo_decoder as md
import Fiume.state_machine as sm
import Fiume.fiume as fm
import Fiume.utils as utils

##################################################

def main_single():
    options = sm.parser()
    
    with open(options["torrent_path"], "rb") as f:
        metainfo = md.MetaInfo(
            bencodepy.decode(f.read()) | options
        )

    tm = md.TrackerManager(metainfo, options)

    t = sm.ThreadedServer(
        metainfo, tm, 
        **options
    )

    
def main_multiple():
    options = sm.parser()

    app = fm.Fiume(options)
    app.begin_session()
    
# if __name__ == "__main__":
#     main()
