#!/usr/bin/env python3.5
# -*- coding: utf-8 -*-

import requests
import json
import yaml
import sys
import argparse
import os
import pathlib
import configparser

yamlp = lambda x: print(yaml.dump(x, explicit_start=True))

class Netbox:
    Devices = "/dcim/devices/"
    Interfaces = "/dcim/interfaces/"
    VLANs = "/ipam/vlans/"

    def __init__(self, api_url, api_key):
        self.base_url = api_url
        self.api_key = api_key
        self.headers = {
            'Authorization': 'Token ' + self.api_key,
            'Content-Type': 'application/json',
        }

    def get(self, path, arg=""):
        url = self.base_url + path + str(arg)
        return requests.get(url, headers=self.headers).json()
    
    def filter(self, path, args={}, limit=1000):
        args['limit'] = limit
        args = '&'.join([ '{0}={1}'.format(*i) for i in args.items() ])
        path = path + "?" + args
        return self.get(path)['results']


class Port:
    def __init__(self):
        self.shutdown = True
        self.clean = True
        self.type = None
        self.descr = None
        self.mtu = None
        self.lag = None
        self.lagmode = None
        self.untagged = None
        self.tagged = None
    
    def to_dict(self):
        data = {}
        if self.clean:
            data['clean'] = True
            return data
        
        data['shutdown'] = self.shutdown

        if self.type:
            data['type'] = self.type
        if self.descr:
            data['descr'] = self.descr
        if self.mtu:
            data['mtu'] = self.mtu
        if self.lag:
            data['lag'] = self.lag
        if self.lagmode:
            data['lagmode'] = self.lagmode
        if self.untagged:
            data['untagged'] = self.untagged
        if self.tagged:
            data['tagged'] = self.tagged
        
        return data

    def __load_mode_and_vlans(self, o):
        if o['mode']:
            if o['mode']['value'] == "access":
                self.type = "access"
            elif o['mode']['value'] == "tagged":
                self.type = "trunk"
            elif o['mode']['value'] == "tagged-all":
                self.type = "trunk"
                self.untagged = 1
                self.tagged = "all"
            else:
                raise AssertionError("Unknown mode: ", o['mode']['value'])
        
        #
        # set untagged and tagged vlans
        #
        if o['untagged_vlan']:
            self.untagged = o['untagged_vlan']['vid']
        
        if o['tagged_vlans']:
            self.tagged = [ i['vid'] for i in o['tagged_vlans'] ]


    @staticmethod
    def load_from_netbox(netbox, o):
        port = Port()

        if o['connected_endpoint_type'] == None and o['type']['value'] != 'lag':
            port.clean = True
            return port

        port.clean = False
        
        #
        # set descr
        #
        if o['description']:
            port.descr = o['description']
        elif o['connected_endpoint_type'] == "dcim.interface":
            port.descr = "%s:%s" % (
                o['connected_endpoint']['device']['name'],
                o['connected_endpoint']['name']
                )
        elif o['connected_endpoint_type'] == "circuits.circuittermination":
            port.descr = "%s" % (
                o['connected_endpoint']['circuit']['cid']
                )
        else:
            raise AssertionError("Unknown connected_endpoint_type: ", o['connected_endpoint_type'])

        #
        # set shutdown state
        #
        port.shutdown = o['enabled'] == False

        #
        # set lag
        #
        if o['lag']:
            lag_number = None
            if o['lag']['name'].startswith("Port-channel"):
                lag_number = int(o['lag']['name'][12:])
            else:
                raise AssertionError('Cannot parse link-aggregation number from parent lag interface name')
            port.lag = lag_number
            port.lagmode = "active"

            lag_iface = netbox.get(Netbox.Interfaces, o['lag']['id'])

            port.__load_mode_and_vlans(lag_iface)

        port.__load_mode_and_vlans(o)

        #
        # set MTU
        #
        if o['mtu']:
            port.mtu = o['mtu']

        return port

def __try_get_device(netbox, device_name):
    try:
        device = netbox.filter(Netbox.Devices, dict(name=device_name))[0]
        return device['id']
    except:
        return None

def _get_device_id(netbox, config, device_name):
    names = [ device_name, "%s.%s" % (device_name, config['search_domain']) ]
    for name in names:
        r = __try_get_device(netbox, name)
        if r:
            return r
    raise AssertionError("Device not found. Tried: " + ', '.join(names))

def get_device_info(netbox, device_id):
    device = netbox.get(Netbox.Devices, device_id)
    context = {}
    context['hostname'] = device['name']
    return context

def get_vlans(netbox):
    vlans = dict(map(
        lambda x: [ x['vid'], dict(name=x['name']) ],
        netbox.filter(Netbox.VLANs)
    ))
    return dict(vlans=vlans)

def get_ports(netbox, device_id):
    data = netbox.filter(Netbox.Interfaces, { 'device_id': device_id })

    ports = {}
    for iface in data:
        name = iface['name']
        ports[name] = Port.load_from_netbox(netbox, iface).to_dict()

    return dict(ports=ports)


def get_users(netbox):
    # TODO
    # for now, use var from settings
    raise NotImplemented("--users not implemented yet")

def main():
    parser = argparse.ArgumentParser()

    default_cfg = os.path.join(pathlib.Path.home(), '.nbnak.cfg')

    parser.add_argument('--config',
        dest='config',
        default=default_cfg,
        type=argparse.FileType('r'),
        help = "config file (default: %(default)s)"
        )
    parser.add_argument('--vlans',
        dest='vlans',
        action='store_true',
        default=False,
        help="Include vlans section."
        )
    parser.add_argument('--users',
        dest='users',
        action='store_true',
        default=False,
        help="Include users section."
        )
    parser.add_argument('--device',
        dest='device',
        default=None,
        help="Include information about specific device."
        )
    parser.add_argument('--ports',
        dest='ports',
        action='store_true',
        default=False,
        help="Include ports configuration of device. Only usable if --device is specified."
        )
    
    options = parser.parse_args(sys.argv[1:])

    config = configparser.ConfigParser()
    config.read(options.config.name)
    config = config['nbnak']

    netbox = Netbox(config['api_url'], config['api_key'])
    context = {}

    device_id = None
    if options.device:
        try:
            device_id = _get_device_id(netbox, config, options.device)
        except Exception as e:
            print(str(e))
            sys.exit(1)
        device = get_device_info(netbox, device_id)
        context.update(device)

    if options.vlans:
        vlans = get_vlans(netbox)
        context.update(vlans)

    if options.ports:
        ports = get_ports(netbox, device_id)
        context.update(ports)

    if options.users:
        users = get_users(netbox)
        context.update(users)

    yamlp(context)


if __name__ == "__main__":
    main()
