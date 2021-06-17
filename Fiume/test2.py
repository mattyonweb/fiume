import time
import queue
import threading
import enum
import dataclasses
import socket
from typing import *  

class Actor(threading.Thread):
    DISPATCHER = dict()
    
    def __init__(self, group=None, target=None, name=None,
                 args=(), queue_in=None, queue_out=None):
        
        threading.Thread.__init__(self, group=group, target=target, name=name)
        
        self.queue_in = queue_in if queue_in is not None else queue.Queue()

        self.kill_event = threading.Event()

    def start(self):
        super().start()
        Actor.DISPATCHER[self] = self.queue_in
        
    def receiver(self):
        """ Receives message from queue_in """
        while True:
            mex = self.queue_in.get()            

            if mex.mtype == MexType.KILL:
                if self.kill_event.is_set():
                    return
                self.shutdown()

            self.react(mex)

            
    def shutdown(self):
        print("Shutdown!")
        self.kill_event.set()
        del Actor.DISPATCHER[self]
        self.queue_in.put(Mex(MexType.KILL, self, None))

        
    def send(self, to: "Actor", mextype, contents: Any):
        """ Puts messages in queue_out """
        mex = Mex(mextype, self, contents)
        
        queue_out = Actor.DISPATCHER[to]
        queue_out.put(mex)

        
    def send_all(self, mextype, contents: Any):
        mex = Mex(mextype, self, contents)
        
        for actor in Actor.DISPATCHER:
            self.send(actor, mextype, contents)

            
    def react(self, mex):
        print(self.name, "Received", mex)

        
    def run(self):
        consumer_thread = threading.Thread(target=self.receiver)
        consumer_thread.start()
        consumer_thread.join()

###############################

class MexType(enum.Enum):
    KILL = 0
    OK = 1
    FORWARD = 2
    CONTROL = 3

@dataclasses.dataclass
class Mex:
    mtype: MexType
    mfrom: Actor
    mcontent: Union[Any, Tuple]

###############################

class EchoActor(Actor):
    def react(self, mex: Mex):
        print(f"[ECHO] From {mex.mfrom.name}: {mex.mtype} - {mex.mcontent}")

class ProducerActor(Actor):
    pass

class SocketWriter(Actor):
    def __init__(self, client_socket, client_address, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.client_socket, self.client_address = client_socket, client_address

    def react(self, mex):
        self.client_socket.send(bytes(str(mex)+"\n", encoding="UTF-8"))

class Remote(Actor): #Inutile
    pass

class SocketReader(Actor):
    def __init__(self, master_thread: Actor, address: Tuple, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.my_address = address
        self.master_thread = master_thread
        self.sock = None
        
    def send(self, ignored, mex_type, contents):
        super().send(self.master_thread, mex_type, contents)
    
    def socket_receive(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(self.my_address)
        print("Binded at ", self.my_address)

        self.sock.listen(2)
        
        while True:
            client_socket, address = self.sock.accept()

            writer_actor = SocketWriter(client_socket, address)
            writer_actor.start()
            
            while True:
                s: str = client_socket.recv(16).decode().strip()
                
                if s == "kill":
                    mex = Mex(MexType.KILL, self, None)
                    self.queue_in.put(mex)
                    return
                elif s == "killall":
                    self.send_all(MexType.KILL, None)
                    self.queue_in.put(mex)
                    return
                else:
                    mex = Mex(MexType.FORWARD, "remote", s)
                    self.queue_in.put(mex)

    def react(self, mex):
        if mex.mtype == MexType.FORWARD:
            self.send(None, MexType.OK, mex.mcontent)
        
        
    def run(self):
        consumer_thread = threading.Thread(target=self.receiver)
        consumer_thread.start()
        self.socket_receive()
        consumer_thread.join()

###############################

thread_main = ProducerActor()
thread_main.start()

t1 = EchoActor(name="attore-1")
t1.start()

thread_socket_reader = SocketReader(t1, ("localhost", 33333))
thread_socket_reader.start()

for i in range(5):
    thread_main.send(t1, MexType.CONTROL, f"ciao! {i}")
    thread_main.send(thread_socket_reader, MexType.CONTROL, f"ciao! {i}")
    time.sleep(5)

