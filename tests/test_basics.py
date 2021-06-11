import unittest
import random
import threading
import time

import Fiume.utils as utils
import Fiume.state_machine as sm

import logging
logging.disable(logging.WARNING)


class BasicsTestCase(unittest.TestCase):
    def test_bitmap_conversion(self):
        a = [True, False, False, True, False, True, True, False]
        self.assertTrue(utils.bool_to_bitmap(a)[0] == 150)

        b = [True, False, False, True, False, True, True]
        self.assertTrue(utils.bool_to_bitmap(b)[0] == 150)

        c = [True, False, False, True, False, True, True, False, True]
        self.assertTrue(utils.bool_to_bitmap(c)[0] == 150 and
                        utils.bool_to_bitmap(c)[1] == 128)
                        
    def test_same_data(self):
        for _ in range(5):
            # Creo peer in attesa di connessioni
            t1 = sm.ThreadedServer(0, thread_timeout=3)
            tt1 = threading.Thread(target=t1.listen, args=(False,))
            tt1.start()
            tt1.join(0.5)

            t1_port = t1.sock.getsockname()[1]

            # Creo peer che si connette all'altro peer
            t2 = sm.ThreadedServer(0, thread_timeout=3)
            tt2 = threading.Thread(target=t2.connect_as_client, args=(t1_port,))
            tt2.start()
            tt2.join(1)

            # Alla fine, i due devono avere gli stessi dati
            self.assertEqual(t1.peer.data, t2.peer.data)

            t1.sock.close()
            t2.sock.close()

    def test_same_data_delay(self):
        for _ in range(5):
            t1 = sm.ThreadedServer(0, thread_timeout=3, thread_delay=0.1)
            tt1 = threading.Thread(target=t1.listen, args=(False,))
            tt1.start()
            tt1.join(0.5)

            t1_port = t1.sock.getsockname()[1]

            t2 = sm.ThreadedServer(0, thread_timeout=3, thread_delay=0.1)
            tt2 = threading.Thread(target=t2.connect_as_client, args=(t1_port,))
            tt2.start()
            tt2.join(1)

            time.sleep(10) # ovviamente non va bene, ci può impiegare di più
            
            self.assertEqual(t1.peer.data, t2.peer.data)

            t1.sock.close()
            t2.sock.close()
