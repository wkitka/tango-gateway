tango-gateway
=============

A Tango gateway server

Clients from other networks can connect to the gateway to access the tango
database transparently. It opens ports dynamically when an access to a device
is required and redirects the traffic to the corresponding device. The ZMQ
tango events are also supported.


Requirements
------------

- python >= 3.4
- zmq
- aiozmq
- pytango (optional)


Usage
-----

```
$ tango-gateway -h
usage: tango-gateway [-h] [--bind ADDRESS] [--port PORT] [--tango HOST]

Run a Tango gateway server

optional arguments:
  -h, --help            show this help message and exit
  --bind ADDRESS, -b ADDRESS
                        Specify the bind address (default is all interfaces)
  --port PORT, -p PORT  Port for the server (default is 8000)
  --tango HOST, -t HOST
                        Tango host (default is given by PyTango)
```

Local testing
-------------

Developer can test this project locally by using minimal control system set up with ``docker-compose``

Requirements:

- docker
- docker-compose

To set up testing environment, run `docker-compose up -d` in project root directory.

To see logs from Tango Gateway in real time, run `docker-compose logs -f gateway`.

Test environment is separated in two netwroks: `net-int` and `net-ext`. `net-int` is internal network for Tango Control System.
It consists of all services needed by Tango Control System like DatabaseDs, Jive, etc.
Other network, `net-ext`, is separated from control system. Only joint point is `gateway` container. It has access to both networks.
There are to client services, `pytango-in` and `pytango-ext`, both placed in different networks. They can be used for 
connectivity tests.

Contact
-------

KITS : kitscontrol@maxiv.lu.se
