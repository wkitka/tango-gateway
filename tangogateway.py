"""Provide a Tango gateway server."""

import giop
import asyncio
import argparse
from enum import Enum
from functools import partial
from contextlib import closing
import struct

try:
    import PyTango
except ImportError:
    PyTango = None


class Patch(Enum):
    NONE = 0
    IOR = 1
    CSD = 2
    ZMQ = 3
    SUB = 4


class HandlerType(Enum):
    DB = 1
    DS = 2
    ZMQ = 3


class Origin(Enum):
    CLIENT = 1
    DS = 2


CLIENT_COUNT = 0
CHECK_PORTS = []  # Fill up for debugging purposes
IMPORT_DEVICE = b'DbImportDevice'
GET_CSDB_SERVER = b'DbGetCSDbServerList'
ZMQ_SUBSCRIPTION_CHANGE = b'ZmqEventSubscriptionChange'


# Debug

def find_ports(frame, ports=CHECK_PORTS):
    return [port for port in ports if find_port(port, frame)]


def find_port(port, frame):
    port_str = struct.pack("H", port)
    port_byte = str(port).encode()
    ascii_str = giop.bytes_to_ascii(port_str)
    ascii_byte = giop.bytes_to_ascii(port_byte)
    return any(x in frame
               for x in [port_str, port_byte, ascii_str, ascii_byte])


# String helper

def find_all(string, sub):
    start = 0
    while True:
        start = string.find(sub, start)
        if start == -1:
            return
        yield start
        start += len(sub)


# Coroutine helpers

@asyncio.coroutine
def read_giop_frame(reader, bind_address, patch=Patch.NONE, debug=False):
    # Read header
    loop = reader._loop
    try:
        raw_header = yield from reader.readexactly(12)
    except asyncio.IncompleteReadError:
        return b''
    header = giop.unpack_giop_header(raw_header)
    # Read data
    raw_data = yield from reader.readexactly(header.size)
    raw_frame = raw_header + raw_data
    if header.message_type != giop.MessageType.Reply or patch == Patch.NONE:
        return raw_frame
    # Unpack reply
    raw_reply_header, raw_body = raw_data[:12], raw_data[12:]
    reply_header = giop.unpack_reply_header(raw_reply_header)
    if reply_header.reply_status != giop.ReplyStatus.NoException or \
       header.order != giop.LITTLE_ENDIAN:
        return raw_frame
    # Patch body
    if patch == Patch.IOR:
        new_body = yield from check_ior(raw_body, bind_address, loop)
    elif patch == Patch.ZMQ:
        new_body = yield from check_zmq(raw_body, bind_address, loop)
    elif patch == Patch.CSD:
        new_body = yield from check_csd(raw_body, bind_address, loop)
    # Ignore
    if not new_body:
        return raw_frame
    # Repack frame
    raw_data = raw_reply_header + new_body
    return giop.pack_giop(header, raw_data)


@asyncio.coroutine
def start_forward(host, port, bind_address, handler_type, loop):
    handler_dict = {
        HandlerType.DS: handle_ds_client,
        HandlerType.ZMQ: handle_zmq_client}
    # Start port forwarding
    func = handler_dict[handler_type]
    handler = partial(func, host=host, port=port)
    server = yield from asyncio.start_server(
        handler, bind_address, 0, loop=loop)
    value = (
        server,
        server.sockets[0].getsockname()[0],
        server.sockets[0].getsockname()[1],)
    msg = "Forwarding {0[0]} port {0[1]} to {1[0]} port {1[1]}..."
    print(msg.format(value[1:], (host, port)))
    return value


# Inspect DB traffic

@asyncio.coroutine
def handle_db_client(reader, writer, host, port):
    with closing(writer):
        bind_address = writer._transport._sock.getsockname()[0]
        db_reader, db_writer = yield from asyncio.open_connection(
            host, port, loop=reader._loop)
        with closing(db_writer):
            while not reader.at_eof() and not db_reader.at_eof():
                # Read request
                request = yield from read_giop_frame(reader, bind_address)
                if not request:
                    break
                db_writer.write(request)
                # Choose patch
                if IMPORT_DEVICE in request:
                    patch = Patch.IOR
                elif GET_CSDB_SERVER in request:
                    patch = Patch.CSD
                else:
                    patch = Patch.NONE
                # Read reply_header
                reply = yield from read_giop_frame(
                    db_reader, bind_address, patch=patch)
                writer.write(reply)
                print(find_ports(reply))
                if find_ports(reply):
                    giop.print_bytes(reply)
                    print(reply)


@asyncio.coroutine
def check_ior(raw_body, bind_address, loop):
    # Find IOR, host and port
    ior = giop.find_ior(raw_body)
    if not ior:
        return False
    ior, start, stop = ior
    host = ior.host[:-1].decode()
    key = host, ior.port, bind_address
    # Start port forwarding
    if key not in loop.forward_dict:
        value = yield from start_forward(
            host, ior.port, bind_address, HandlerType.DS, loop)
        loop.forward_dict[key] = value
    # Patch IOR
    server, host, port = loop.forward_dict[key]
    ior = ior._replace(host=host.encode() + giop.STRING_TERM, port=port)
    # Repack body
    return giop.repack_ior(raw_body, ior, start, stop)


@asyncio.coroutine
def check_csd(raw_body, bind_address, loop):
    csd = giop.find_csd(raw_body)
    if not csd:
        return False
    csd, start = csd
    new_csd = ':'.join((bind_address, loop.server_port))
    new_csd = new_csd.encode() + giop.STRING_TERM
    return giop.repack_csd(raw_body, new_csd, start)


# Inspect DS traffic

@asyncio.coroutine
def handle_ds_client(reader, writer, host, port):
    with closing(writer):
        bind_address = writer._transport._sock.getsockname()[0]
        ds_reader, ds_writer = yield from asyncio.open_connection(
            host, port, loop=reader._loop)
        with closing(ds_writer):
            while not reader.at_eof() and not ds_reader.at_eof():
                # Read request
                request = yield from read_giop_frame(reader, bind_address)
                if not request:
                    break
                ds_writer.write(request)
                # Choose patch
                if ZMQ_SUBSCRIPTION_CHANGE in request:
                    patch = Patch.ZMQ
                else:
                    patch = Patch.NONE
                print(patch)
                # Read reply_header
                reply = yield from read_giop_frame(
                    ds_reader, bind_address, patch=patch)
                writer.write(reply)


@asyncio.coroutine
def check_zmq(raw_body, bind_address, loop):
    # Find zmq token
    zmq = giop.find_zmq_endpoints(raw_body)
    if not zmq:
        return False
    # Exctract endpoints
    new_endpoints = []
    zmq1, zmq2, start = zmq
    for zmq in (zmq1, zmq2):
        host, port = giop.decode_zmq_endpoint(zmq)
        key = host, port, bind_address
        # Start port forwarding
        if key not in loop.forward_dict:
            value = yield from start_forward(
                host, port, bind_address, HandlerType.ZMQ, loop)
            loop.forward_dict[key] = value
        # Make new endpoints
        server, host, port = loop.forward_dict[key]
        new_endpoints.append(giop.encode_zmq_endpoint(host, port))
    # Repack body
    zmq1, zmq2 = new_endpoints
    return giop.repack_zmq_endpoints(raw_body, zmq1, zmq2, start)


# Inspect ZMQ traffic

@asyncio.coroutine
def handle_zmq_client(client_reader, client_writer, host, port):
    ds_reader, ds_writer = yield from asyncio.open_connection(host, port)
    # Debug
    global CLIENT_COUNT
    CLIENT_COUNT += 1
    c_host, c_port = client_reader._transport._sock.getsockname()
    s_host, s_port = ds_reader._transport._sock.getpeername()
    client = ':'.join((c_host, str(c_port))) + " <{}>".format(CLIENT_COUNT)
    server = ':'.join((s_host, str(s_port)))
    desc1 = client + ' -> ' + server
    desc2 = server + ' -> ' + client
    # ...
    task1 = inspect_pipe(client_reader, ds_writer, Origin.CLIENT, debug=desc1)
    task2 = inspect_pipe(ds_reader, client_writer, Origin.DS, debug=desc2)
    yield from asyncio.gather(task1, task2)


@asyncio.coroutine
def inspect_pipe(reader, writer, origin, debug=False):
    bind_address = writer._transport._sock.getsockname()[0]
    with closing(writer):
        while not reader.at_eof():
            data = yield from read_zmq_frame(reader, bind_address, origin)
            if debug and data:
                print(debug.center(len(debug) + 2).center(60, '#'))
            writer.write(data)


@asyncio.coroutine
def read_zmq_frame(reader, bind_address, origin):
    loop = reader._loop
    # Get new db
    if origin == Origin.CLIENT:
        new_db = ':'.join(map(str, loop.tango_host)).encode()
    else:
        new_db = ':'.join((bind_address, loop.server_port)).encode()
    # Read frame
    body = yield from reader.read(4096)
    changes = []
    for index in find_all(body, b'tango://'):
        start = index-2 if origin == Origin.CLIENT else index-1
        size = body[start]
        stop = index+size-1
        read = body[start+1:stop]
        prot, empty, db, *device = read.split(b'/')
        new_read = b'/'.join((prot, empty, new_db) + tuple(device))
        changes.append((start, stop, bytes([len(new_read)]) + new_read))
    # No changes
    if not changes:
        return body
    # Apply changes
    new_body, prev = b'', 0
    for start, stop, change in changes:
        new_body += body[prev:start] + change
        prev = stop
    new_body += body[prev:]
    # Return
    return new_body


# Run server

def run_server(bind_address, server_port, tango_host):
    """Run a Tango gateway server."""
    # Initialize loop
    loop = asyncio.get_event_loop()
    loop.bind_address = bind_address
    loop.server_port = server_port
    loop.tango_host = tango_host
    loop.forward_dict = {}
    # Create server
    host, port = tango_host
    handler = partial(handle_db_client, host=host, port=port)
    coro = asyncio.start_server(handler, bind_address, server_port)
    server = loop.run_until_complete(coro)
    # Serve requests until Ctrl+C is pressed
    msg = ('Serving a Tango gateway to {0[0]} port {0[1]} '
           'on {1[0]} port {1[1]} ...')
    print(msg.format(loop.tango_host, server.sockets[0].getsockname()))
    try:
        loop.run_forever()
    except KeyboardInterrupt:
        pass
    # Close all the servers
    servers = [server for server, host, port in loop.forward_dict.values()]
    servers.append(server)
    for server in servers:
        server.close()
    # Wait for the servers to close
    wait_servers = asyncio.wait([server.wait_closed() for server in servers])
    loop.run_until_complete(wait_servers)
    # Cancel all the tasks
    tasks = asyncio.Task.all_tasks()
    for task in tasks:
        task.cancel()
    # Wait for all the tasks to finish
    if tasks:
        loop.run_until_complete(asyncio.wait(tasks))
    loop.close()


def main(*args):
    """Run a Tango gateway server from CLI arguments."""
    # Create parser
    parser = argparse.ArgumentParser(description='Run a Tango gateway server.')
    parser.add_argument('--bind', '-b', metavar='ADDRESS', default='',
                        help='Specify the bind address '
                        '(default is all interfaces)')
    parser.add_argument('--port', '-p', metavar='PORT', default=8000,
                        help='Port for the server (default is 8000)')
    parser.add_argument('--tango', '-t', metavar='HOST',
                        help='Tango host (default is given by PyTango)')
    # Parse arguments
    namespace = parser.parse_args(*args)
    # Check Tango database
    if PyTango is None:
        if namespace.tango:
            print("Warning: PyTango not available, cannot check database")
            namespace.tango = namespace.tango.split(":")
        else:
            parser.error("PyTango not available, "
                         "the tango host has to be defined explicitely")
    else:
        if namespace.tango:
            db = PyTango.Database(namespace.tango)
        else:
            db = PyTango.Database()
        namespace.tango = db.get_db_host(), int(db.get_db_port())
    # Run the server
    return run_server(namespace.bind, namespace.port, namespace.tango)


if __name__ == '__main__':
    main()
