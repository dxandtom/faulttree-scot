# -*- coding: utf-8 -*-
from os import remove, path, walk, makedirs, listdir
import sys
from struct import unpack
from zipfile import ZipFile
from re import split, I, search, match, S, M, compile, findall, sub
from time import strftime, strptime, gmtime
from chardet import detect
from datetime import datetime as dd
from collections import Counter
try:
    from openpyxl import Workbook
    from openpyxl.styles import Font, Alignment, PatternFill
    HAS_OPENPYXL = True
except ImportError:
    HAS_OPENPYXL = False
    print("Warning: openpyxl not installed. Summary sheet will not be generated.")
#import lxml
#from functools import reduce

import pandas as pd
import traceback
from functools import reduce
import threading
import re
#sys.setdefaultencoding('utf-8')
FIRST_KEYS = ['y', 'q', 'isis-af-topology', 'inner-connect', 'transit-vpn', 'hardware-based', 'lsp-loopback', 'md', 'domain-location', 'pce-client-connect', 'msdp', 'abr-summary', 'default-martini', 'alarm-reverse', 'hello', 'lastmember-queryinterval', 'ap-online', 'packet-policy', 'lns', 'dscp', 'preference', 'interface-status-change', 'service6-sec', 'rr-filter', 'dcp', 'subinterface', 'local-ce', 'mbandwidth', 'mpls-rsvp-te-peer', 'parity', 'rdi', 'flow-queue-template', 'vlan-extend', 'authentication-name', 'traffic-policy', 'receive-tolerance', '50GE', 'hard-pipe', 'explicit-path', 'dhcpv6-server-group', 'route-policy', 'pbb', 'ipv6-pool', 'bi-static-ingress', 'forwarding-simulation', 'task-group', 'spmsi-tunnel', 'index', 'crp-policy', 'network-entity', 'fec', 'wtr', 'loop-detection', 'qos-profile', 'inactive-port', 'mpls-ldp-remote', 'jitter-codec', 'encryption-algorithm', 'static-user', 'access-vlan', 'signal-coding', 'execute-default', 'statistics', 'aggregate', 'share-shaping', 'bgp-af-vpn-target', 'link-detect', 'tcp-kind', 'block', 'ascii', 'supernet', 'gvrp', 'default-domain', 'install-module', 'user-interface', 'eap-sim-parameter', 'apply-label', 'client-id', 'patch-diagnose', 'server-port', 'flow-car', 'stub-router', 'vpn-instance-af-ipv6', 'rate-limit-mode', 'source-interface', 'command', 'stream-dual-receiving', 'backup-id', 'vpls-multicast', 'lmp-peer', 'bgp-af-ipv6-flow', 'pd-unshare-only', 'sys-ipsec-policy-template', 'release-agent', 'operation-type', '802.1p-to-vpn', 'pim6', 'pfs', 'process-pst', 'bgp-lsp', 'access-speed', 'local-switch', 'record-file', 'Virtual-Serial', 'nas-serial', 'ddp', 'cspf', 'ike-peer', 'l2-channel-glb', 'mput', 'ds-lite', 'unr', 'access-type', 'authorization-scheme', 'route-filter', 'ike-proposal', 'VC4', 'traffic-statistics', 'mrru', 'port-wred', 'duplex', 'dhcp-user-slot-warning-threshold', 'pure-user-name', 'sequence', 'rule-admin', 'sr-te', 'adv-factor', 'queue-4cos-mapping', 'ssm-map', 'quota-out', 'observe', 'service-sec', 'test', 'domain-list', 'ssh', 'default-route-advertise', 'arp-ping', 'Global-Ima-Group', 'vsi-bgpad', 'ccm', 'rule-name', 'radius-server', 'client-duid', 'binding', 'ip-address', 'cu-mode', 'ti-lfa', 'pki-access-policy', 'enable', 'send-trap', 'pw-tag', 'virtual-access-portextend', 'differential-delay', 'ike-version', 'flash-flood', 'emdi', 'import-source', 'dhcpv6-only', 'quit', 'slow-peer', 'isis-frr', 'cgn-pub-user', 'section', 'multi-instance', 'lsa-arrival-interval', 'loopback-detect', 'reroute', 'dns-url', 'ui-vty', 'bu-suppression', 'backup-rpf-switchover-delay', 'association-type', 'l3-loop-detect', 'option121', 'ipsec-profile', 'frame-format', 'ssm-map6', 'said-node', 'segment-routing', 'mapping-path', 'dhcp-decline', 'help', 'fes', 'tcpsyn-flood', 'as-notation', 'helper-role', 'encapsulation', 'authorization-cmd', 'remote-address', 'service-instance-group', 'qos-profile-timerange', 'link-state', 'login-failed', 'sham-hello', 'Sip', 'startup', 'alarm-mask', 'Virtual-Ethernet', 'T1', 'constraint-rt-filter', 'Nve', 'vlan-host-car', 'data-switch', 'IfmMpIf', 'ecc-public-key', 'map-t-instance', 'vbridge', 'lsr-id', 've-group', 'asbr-summary', 'lpu-group', 'ebgp-interface-sensitive', 'realm-name-delimiter', 'cost-offset', 'ipv4', 'sbfd', 'dsa', 'member-slot', 'min-tx-interval', '50100GE', 'wait-request-time', 'local-mac', 'Cpos-Trunk', 'interface-name', 'idle-code', 'direct-route', 'dustproof', 'netconf-nacm-rule-list-rule', 'l2tp-user', 'tiebreaker', 'link-id', 'remote-backup-profile', 'pce-client', 'backup-dual-receiving', 'mac', 'route-mode', 'calling-number-avp', 'reverse', 'portal-redirect', 'tariff-level-cfg', '200GE', 'script-assistant', 'xpl-rfg', 'xpl-pfx', 'dcn', 'holdtime', 'measure-point', 'bpdu', 'register-source', 'te-class7', 'http-redirect-profile', 'GE', 'save', 'area', 'idle-user-redirect', 'authentication-method-ipv6', 'ma', 'pc-af-map', 'rpf-vector', 'ethernet', 'fast-terminate', 'upBoard', 'qppb-loacl-policy', 'match-tunnel', 'slaac-unshare-only', 'prefix-priority', 'static-bind', 'udp', 'statistics-cycle', 'assistant-cli', 'traffic-policing', 'import', 'ServiceIf', 'ptp-adaptive', 'port-group', 'network', 'avoid-microloop', 'atm-pack-overtime', 'vrrp', 'broadcast-copy', 'user-enable-group', 'erps', 'qos', 'client', 'prefix-assign-mode', 'ima', 'l2tp-authorize', 'hvrp-transport', 'attached-bit', 'tftp-server', 'mst-process-region', 'sm2', 'qppb-policy', 'telnet', 'optimize-algorithm', 'alarm-threshold', 'pwsignal', 'search', 'static-rpf-peer', 'bfd-detect', 'confederation', 'static-route', 'echo-passive', 'aaa-task-group', 'mpls-oam', 'sys-b-evpn', 'mst-process', 'tunnel-id', 'twamp', 'DcnInterface', 'vrrp-id', 'history', 'country', 'rmon', 'private-4-byte-as', 'register-packet-checksum', 'flow-route', 'lsp-exp', 'locator', 'ipoe-server', 'trace', 'vpls-id', 'virtual-access-interface', 'mldp', 'lsp-fragments-extend', 'color-flag', 'forbidden', 'rule-type', 'license', 'port-access-limit', 'rip', 'trunkport', 'cmu-alarm', 'access-limit', 'hotstandby-switch', 'Vlanif', 'responder', 'frr', 'exchange-port', 'qos-mshaping', 'silent-interface', 'abort', 'ike', 'pppoe-server', 'redirect-cpu', 'Virtual-Template', 'wavelength-channel', 'VC12', 'c-rp', 'reboot', 'resv-style', 'traffic-eng', 'revertive', 'lpt', 'as', 'smart-link', 'multicast-suppression', 'dhcpv6-unshare-only', 'port-type', 'user-defined-flow', 'mqc', 'capture-packet', 'bgp-ls', 'ignore-mtu-match', 'flow-mapping', 'FlexE50100G', 'user-vlan', 'va-explicit-path-hop', 'dh', 'vsi-bgp-pw', 'mirror', 'key-string', 'key-id', 'join-prune', 'zero-metric-check', 'bgp-vpn', 'encapsulation-mode', 'user-basic-service-ip-type', 'control-vlan', 'organization', 'isis-link-group', 'vpws-df-election', 'pw-control-word', 'rule', 'vlan-group-trunk', 'timeout', 'dns-search-list', 'suppress-flapping', 'pim', 'vlan-group-fe', 'bas-interface-name', 'arp-safeguard', 'option82-identify', '802.1p', 'preferred-control-word', 'dot1q', 'flow-interface-group', 'external-path', 'uservlan_QinQvlan', 'twoway-loss', 'monitor-group', 'user-priority', 'application-apperceive', 'vpn-instance-af-ipv4-mvpn-ipmsi-tunnel-mpls-te', 'snetconf', 'twamp-light', 'unicast-route', 'service-template', 'tunnel-acct-2867', 'import-channel', 'FlexE', 'target', 'authentication-algorithm', 'loop-detect', 'open', 'node-id', 'transport-address', 'datafill', 'l2-channel-vsi', 'hostdefend', 'filter-lsa-out', 'realm-location', 'authening', 'area-authentication-mode', 'renew-time-percent', 'threshold', 'test-flow', 'domain-id', 'protocol', 'nas', 'pic', 'error-down', 'entropy-label', 'speed', 'optical-tx-power', 'inclusive-provider-tunnel', 'sip-server', 'tunnel-alarm', 'configuration', 'value-added-service', 'bgp-af-vpnv4-multicast', 'fragment-threshold', 'p2p-vsi', 'frame-ipv6', 'basinfo-insert', 'dsdomain', 'switch-mode', 'frame-length', 'send-message', 'is-level', 'admin-vsi', 'email', 'acl-mpls', 'user-group', 'gre', 'suppression', 'bas', 'source-policy', 'ftp-client', 'outbound', 'layer3-subscriber', 'remote-mep', 'lock', 'radius-service', 'monitor6', 'rrpp', 'ne-ip', 'seamless-bfd', 'pnp-package', 'tunnel-selector', 'http-redirect', 'sac', 'sensor-alarm', 'ccm-interval', 'ssid', 'cpos', 'sysname', 'local-service-id', 'datasize', 'rm', 'delay-up', 'acl-ip-pool', 'rpd-family', 'xpl-pfx6', 'aaa-domain', 'opaque-capability', 'summary', 'lcd', 'route', 'test-type', 'ipsec', 'acl-ipv6-pool', 'flow-queue', 'bpdu-tunnel', 'lsa-originate-interval', 'record6', 'register-policy', 'led-flash', 'xpl-community', 'cmu-debug', 'mp', '8021p-outbound', 'serviceid-policy', 'ssl-auth-fail', 'esn', 'lsdb', 'vsi-ipmsi-leaf', 'name', 'client-option18', 'detect', 'permit', 'protocol-group', 'online-exmbist', 'query', 'md5-password', 'te-p2p-template', 'increase-priority', 'path-constraint', 'gre-tunnel-group', 'bgp-af-vpn6-instance', 'hold-up', 'vlink-peer', 'fm', 'config-template', 'erps-ring', 'user-policy', 'in-group', 'echo-interval', 'bgp-af-ls', 'user-max-session', 'rtp-header', 'protect-mode', 'ip-trigger', 'nqa-jitter', 'opcode', 'mpls-exp-outbound', 'FlexE50G', 'management', 'te-class4', 'option-82', 'max-ipuser-reauthtime', 'bgp-af-ipv6', 'destination-address', 'ignore-stp-loopcheck', 'ospf', 'ipv6-enhance', 'flow-bill', 'kill', 'twoway-delay', 'traffic-statistic', 'aftr-name', 'jumboframe', 'test-id', 'state', 'access-ip-limit', 'protected-instance', 'remote-peer', 'screen-length', 'acl-l2', 'clear-records', 'match-type', 'min-echo-rx-interval', 'FlexE100G', 'te-class2', 'cmu-relay', 'vpn-instance-af-ipv4-mvpn-ipmsi-tunnel', 'PW-VE', 'multicastbandwidth', 'user-traffic-statistic', 'vsi-bgp', 'option60', 'split-horizon', 'service-type', 'keychain-keyid', 'test-failtimes', 'jitter-packetnum', 'tcp-algorithm-id', 'service-enhance', 'correlation-analyze', 'lsp-trigger', 'netconf-nacm-rule-list', 'remotehelp', 'vsi-ipmsi-root', 'vp', 'password', 'router', 'lsa-age', 'default-route', 'queue', 'activate', 'priority', 'port-mirroring', 'mac-address-usage', 'trust', 'mkdir', 'flowspec', 'file', 'next', 'Lmpif', 'safe-sync', 'user-security-policy', 'controller', 'option82-relay-mode', 'tcp-mss', 'eap-end', 'cmdalias', 'mld-snooping', 'wlan-switch', 'dns-server', 'access', 'flow-label', 'user-fqdn', 'SerLoc', 'certificate', 'http-hostcar', 'interval', 'remote-ce', 'reauthentication', 'undelete', 'tariff-level', 'task-type', 'nexthop', 'pim-ipv6', 'default-password', 'explicit-null-label', 'code', 'mirror-instance', 'holdoff-timer', 'igmp', 'vrrp-backup', 'alias', 'remote-ip', 'aaa-user-group', 'minimal', 'dcp-inst', 'output-alarm', 'l2-loop-detect', 'ipfpm', 'interface-list', 'frr-policy', 'mtu', 'snmp', 'mvpn', 'dot1x', 'l2', 'tti', 'map', 'bgp-flow-ipv6-vpn-instance', 'ecc', 'rename', 'cmp', 'mpls-tp', 'integrity-algorithm', 'transmission-alarm', 'untagged', 'Gmpls-Uni', 'host-route', 'l2vpn-family', 'fail-percent', 'vpn-instance-af-ipv4', 'trafficpolicy', 'l2-multicast', 'multicast-vlan', 'vrrp6', 'peer-public-key', 'fan', 'srv', 'tc-protection', 'control-session', 'ipv6-warning-threshold', 'nas-port-type', 'client-option60', 'arp-trigger', 'nqa-sla', 'spt-only', 'rpf-prune-delay', 'http', 'crc-error', 'accounting', 'Pos', 'compare-different-as-med', 'mac-limit', 'schedule-trace', 'service', 'cusp-agent', 'prompt', 'flow-route-ipv6', 'attack-detect', 'te-p2mp-template', 'eca-route-type', 'data-link', 'mac-learn-style', 'channel-set', 'car', 'bgp-vrf-session', 'process-sequence', 'flow-queue-4cos-template', 'Ip-Trunk', 'spt-switch-threshold', 'lease', 'isis', 'ssl-policy', 'unspecified-channel', 'check', 'diffserv', 'bgp-af-rpd', 'conflict', 'queue-depth', 'ma-defend', 'crc', 'holdoff', 'lsp-replymode', 'filter-policy', 'permit-domain-list', 'http-url', 'service-policy', 'flow-queue-priomode-template', 'lsp-tetunnel', 'drop-timeout', 'receive-time', 'control-vid', 'flow-route-ipv6-vpn-instance', 'vpn-instance-af-ipv6-mvpn-spmsi-tunnel', 'otn', 'flow-wred-template', 'deny-domain-list', 'cost-style', 'option33', 'dhcp-forcerenew', 'unknown-multicast', 'laser', 'retry', 'acl6-basic', 'tracert-hopfailtimes', 'share-mode', 'mac-authentication', 'unknown-unicast', 'ap-id', 'dhcp-proxy', 'software', 'register-suppression-timeout', 'lsdb-overflow-limit', 'tracert', 'link', 'sftp', 'pppoe-motm', 'mpls-ldp-vpn-instance', 'udp-packet-defend', 'voltage-threshold', 'domain', 'mandatory-lcp', 'revert-members', 'dns-suffix', 'mpls-inner-exp', 'rewrite', 'state-refresh-rate-limit', 'inter-area-segmented', 'Otn', 'goto', 'web-url', 'ipv6-family', 'port-wred-template', 'trouble-shooting', 'hub-mode', 'connect-server', 'vpn-instance', 'tftp', 'access-line-id', 'rpc-message', 'security-key', 'prefix', 'ais', '25GE', 'bgp-af-mvpn', 'bfd', 'vni', 'ingress-lsp', 'nbns', 'sftp-client', 'ftp-server', 'topology', 'netconf', 'vlan-group', 'simulate', 'vlan-miss', 'ipv4-prefix', 'cc', 'interface-mtu', 'version', 'VX-TUNNEL', 'Ima-group', 'auto-rp', 'schedule', 'atm-bundle', 'ipv6-flow', 'bgp-af-evpn', 'proposal', 'virtual-system', 'netconf-nacm-group', 'channel-group', 'combined-sysname', 'add', 'te-set-subtlv', 'link-quality', 'vlan-type', 'time-range', 'icmp-jitter-mode', 'virtual-cluster-access', 'capability-announcement', 'accept', 'user-queue-resource', 'location', 'igp-sync-delay', 'down-when-looped', 'xpl-as', 'domain-map-list', 'flow-statistic', 'verbose', 'icmp-reply', 'sender', 'bill-server', 'gre-group', 'user-bandwidth', 'ldp-sync', 'rule-list-name', 'stelnet', 'acl6-ucl', 'run', 'remote-pw-id', 'ipv6', 'l3vpn', 'bandwidth', 'maxage-lsa', 'sensor-path', 'vendor-class', 'diameter-group', 'color-mode', 'ma-defend-slot', 'autoroute', 'remote-interface', 'tos', 'ssm-mapping', 'packet-reassembly', 'entropy-label-capability', 'diagnose', 'isis-topology', 'realtime-accounting', 'l2vpn', 'algorithm', 'vsi-pwsignal-ldp-pg', 'cut', 'peer-address', 'lsp-nexthop', 'path', 'excluded-ipv6-prefix', 'guard-timer', 'lifetime', 'get', 'lost-measure', 'bgp-flow-vpn-instance', 'process-interface-status', 'power', 'upgrade', 'port-speed', 'routing-table', 'require-router-alert', 'keychain', 'option37-relay-mode', 'local-address', 'records', 'append', 'pki-attribute-group', 'promode', 'ip', 'network-header-length', 'ext-community-change', 'bd', 'rpt-spt', 'switch-delay', 'default', 'pnp', 'IfmTrunkSerialPpp', 'remark', 'link-address', 'oam-bind', 'L2tpv3Inst', 'load-balancing', 'LoopBack', 'bypass', 'fail-ratio', 'trunk-member', 'IfmSerialHdlc', 'broadcast', 'ping', 'vlan', 'billing-server', 'IfmGlobalMpGroupIf', 'sys-bd-evpn', 'te-link', 'p2mp', 'auto-frr', 'access-delay', 'ospfv3-area', 'primary', 'mpls-te-leaflist', 'mpls-te', 'dot1x-template', 'cpos-trunk', 'adm', 'bidirectional', 'loop-free-alternate', 'web-logfile', 'delete', 'protocol-statistics', 'policy-route', 'E3', 'ap-user', 'target-bandwidth', 'vpn-instance-capability', 'target-board', 'user-instance', 'nat', 'global', 'nqa-server', 'Mtunnel', 'bypass-attributes', 'ops', 'probe-interval', 'FlexE200GE', 'dns-redirect', 'web-redirect', 'set', 'vpn-id', 'source-ip', 'c-multicast', 'tariff-queue-mapping', 'access-user', 'digest-length', 'virtual-channel', 'grpc', 'remote-lfa', 'threshold-alarm', 'group-member', 'cusp', 'daa-user', 'bi-static-egress', 'proxy-egress', 'clock', 'bsr-policy', 'mpls-tp-meg', 'IfEthWan', 'ipsec-sa', 'ach', 'portal-server', 'passive-session', 'bgp-vpn-instance-af-ipv4-label-unicast', 'maximum-routes', 'me', 'ma-defend-interface', 'sub-port-queue', 'access-operation', 'loopback', 'destination-port', 'jitter-buffer-cep', 'lmp', 'realmname-parse-direction', 'fast-reroute', 'aggregation', 'port-observing', 'sub-session', 'wavelength', 'management-acl', 'dhcp-server-group', 'local-cross-routing', 'pbr-policy-node', 'va-pw', 'encap-data-enable', 'bgp-af-ipv4-flow', 'e-trunk', 'queue-priority', 'fragment-flood', 'list', 'Fabric-Port', 'robust-count', 'ip-dscp-inbound', 'link-account', 'default-ip-address', 'speed-limit', 'time-delay-detect', 'tos-exp', 'accounting-copy', 'Cpos', 'suppress-reachability', 'nssa', 'sma', 'lsp-type', 'link-protocol', 'dns', 'vpls-pruning-mode', 'upload-config', 'tie-breaking', 'session-group', 'local-bill', 'tnl-policy', 'bgp', 'interface-bandwidth-type', 'user-block', 'propagate', 'flexe', 'traffic-type', 'admin-vrrp6', 'raps-mel', 'observe-filter', 'option', 'es', 'lsp-path-check', 'leaf', 'pm-server', 'ce-ping', 'dhcpv6', 'aaa-author', 'optional-checksum', 'system', 'carrier', 'segment-routing-ipv6', 'timers', 'lsp-length', 'arp-proxy', 'purge-originator-identification', 'te-class-mapping', 'ipsec-transform-ah', 'vbas', 'root-ip', 'diameter', 'lac', 'dsa-public-key', 'esi', 'dsa-key-code', 'pki-domain-pki-cmp-session', 'export', 'ref', 'retransmission-limit', 'tunnel-prefer', 'xpl-filter', 'sendpacket', 'ipv6-trigger', 'tunnel-withdraw-delay', 'virtual-cluster-chassis', 'mep', 'ftn', 'vpn-instance-af-ipv6-mvpn-ipmsi-tunnel', 'session-state-trap', 'local-aaa-server', 'rm-backup-prf', 'if-match', 'pe-vid', 'info-center', 'bandwidth-reference', 'authentication-mode', 'hop-limit', 'route-monitor-group', 'resource', 'shutdown', 'ipv4-labeled-unicast', 'ah', 'detect-multiplier', 'negotiation', 'FC', 'hwtacacs-server', 'dn-bit-check', 'damping', 'domainlist', 'renewal-time', 'user-password', 'tunnel-per-user', 'packet-priority', 'bas-load-balance', 'local-remote', 'dhcpsnp-whitelist', 'hot-standby', 'portswitch', 'copy', 'os', 'graceful-delete', 'web-auth-server', 'dhcp-stb', 'icmp-broadcast-address-echo', 'binary', 'acl-port-pool', 'vsi-ipmsi-root-te', 'bgp-af-flow-vpnv6', 'match', 'roam-character', 'ipv4-family', 'frame-ip', 'pw', 'apply-command-group', 'web', 'snmp-agent', 'domain-name-delimiter', 'sysmac', 'assistant', 'Vp', 'user-disable-group', '100GE', 'port-queue-template', 'destination-group', 'rpki-session', 'protocol-packets', 'cpu-defend-policy', 'ikev2', 'ap', 'response-ac-state', 'aaa-recording', 'slot', 'attr-set', 'tracert-livetime', 'adminuser-priority', 'radius-group', 'load', 'max', 'upload', 'va-tunnel', 'accounting-start-delay', 'diameter-server', 'high-temperature', 'l2vpn-ad-family', 'label', 'attribute', 'mget', 'ip-block', 'classifier', 'bridge-domain', 'bgp-af-mvpnv6', 'ATM', 'flow-wred', 'service-identify-policy', 'ip-type', 'acl6-if', 'pki', 'loadbalance', 'wtr-timer', 'vsi-ipmsi', 'eth-test', 'recycle', 'Ethernet', 'vsi-pwsignal-ldp', 'port', 'authentication', 'pppoe-url', 'tunnel-detect-delay', 'disorder', 'discovery-pmtu-max', 'debug', 'group', 'power-supply', 'state-refresh-ttl', 'pack', 'tunnel-policy', 'sys-vpws-evpn', 'modify', 'aaa-authen', 're-authentication', 'multicast-virtual-scheduling', 'te-class3', 'vlan-group-gve', 'pw-aps', 'acl6-advance', 'wlan', 'label-type', 'app-sec-global', 'service-group', 'testbus-selftest', 'duration', 'close', 'remote-download', 'filter', 'pki-domain', 'reflector', 'client-access-line-id', 'data-packet', 'spf', 'time', 'web-url-profile', 'sys-ipsec-policy', 'module-name', 'ipmsi-tunnel', 'condition', 'MEth', 'NULL', 'E1', 'control-tunnel', 'tunnel-protocol', 'lacp', 'local-source-address', 'payload-compression', 'route-select', 'statistics-timer', 'cache-sa-enable', 'test-aaa', 'aps', 'va-explicit-path', 'verification', 'af-mul', 'radius-test-group', 'mac-learning', 'acl', 'acl4-advance', '400G', 'command-privilege', 'format', 'local-mt', 'l3ve', 'vpn-orf', 'static-rp', 'poison-reverse', 'backup', 'efm', 'Pos-Trunk', 'as-path-limit', 'execute', 'rpf', 'bgp-af-ipv4', 'xpl-interface', 'dn', 'nd-trigger', 'multicast-bas-policy', 'mcp-inst', 'aps-send', 'nacm', 'mapping-mode', 'dhcp-broadcast', 'start', 'attack-trace', 'multicast-domain', 'Eth-Trunk', 'mtu-match', 'pppuser-offline-convert', 'stp', 'ce', 'file-format', 'neighbor-check', 'senderid-tlv-type', 'ppp-force-authtype', 'multicast-instance', 'multicast-list', 'ppp', 'static-cr-lsp', 'statistics-task', 'isolate', 'user-group-queue', 'import-rib', 'controller-ip', 'ipsec-proposal', 'exclude', 'site', 'flexeth', 'search-target', 'security', 'pim6-anycast-rp', 'remote-aps', 'unknown-unicast-suppression', 'sender-address', 'rpd-policy', 'longest-match', 'control-flap', 'tlp', 'authentication-method', 'flow', 'warning-exhaust', 'arp-fake', 'connection', 'allow-address-change', 'radius-client', 'ptplptPort', 'te', 'slave', 'batch-shutdown', 'dhcpv6-follow-ipv6cp', 'affinity', 'dhcp-server', 'trigger-up-delay', 'mode', 'mmi-mode', 'offline-speed', 'transform', 'roam-domain', '10GE', 'delay-suppression', 'outside-vlan', 'broadcast-suppression', 'IfmViewVc', 'vlan-group-40ge', 'default-password-template', 'vlan-swap', 'ntp-service', 'service-identify', 'flexe-group', 'flow-route-vpn-instance', 'upe-upe', 'stop', 'pim-anycast-rp', 'mcast-mib', 'diffserv-mode', 'detect-active-link', 'connect-mode', 'vrf-route-import', 'ip-trunk', 'remote-backup-service', 'mpls-ldp-ipv4', 'flexethmode', 'link-group', 'exit', 'vlanpvc-to-username', 'set-dscp-outer', 'agetime', 'diameter-link', 'IfmSerialPpp', 'subnet', 'timestamp-unit', 'Tunnel', 'status-code-change', 'excluded-ipv6-address', 'garp', 'stub', 'eth-trunk', 'active-route-advertise', 'distribute-weight', 'short-sequence', 'shell', 'min-members', 'bestroute', 'history-command', 'public-key-code', 'vpn-target', 'ipg', 'min-active-links', 'region-name', 'field', 'trunk', 'multicast-topology', 'test-packet', 'max-response-time', 'ignore-ac-state', 'ipv6-address', 'pki-entity', 'acl4-basic', 'advertise', 'fec-eth', 'url-profile', 'record', 'ipoe-service-type', 'Virtual-ODUk', 'ospfv3-frr', 'qoe', 'pwd', 'track', 'ccc', 'port-alarm', 'nqa-testflow', 'bit-error-detection', 'tunnel', 'aaa-accounting', 'mapping-server', 'mandatory-chap', 'mpls-ldp-vpn-instance-ipv4', 'mpls-passive', 'bgp-local-ifnet', 'nd-proxy', 'server', 'manual-set', 'preempt', 'atm-link', 'optical-module', 'aggregate-vlan', 'flow-control', 'test-session', 'recording-mode', 'grouping-identifier', 'session-group-exclude', 'multicast-vpn', 'ospf-area', 'sham-link', 'register-with-probe', 'exchange-mode', 'gmpls-tunnel', 'pppoe', 'reoptimization', 'recover', 'Vbdif', 'gateway', 'rsa-key-code', 'l2-minst', 'vpn-instance-af-ipv4-mvpn-spmsi-tunnel', 'databits', 'vping', 'dhcp', 'return', 'precision', 'sensor-group', 'check-first-as', 'mru', 'ip-pool-group', 'client-option37', 'reset', 'vsi-bgp-site', 'ipsec-transform-ah-esp', 'ng-mvpn', 'signaling', 'remove', 'evpn', 'site-id', 'primary-root', 'oam-mgr', 'rmdir', 'display', 'user-queue', 'behavior', 'upe-npe', 'peer-ipv6', 'local-pw-type', 'grpc-server', 'fec-list', 'capability', 'path-vectors', 'ap-offline', 'sample-interval', 'dir', 'mcp-inst-ach', 'vpn-group', 'forward', 'Serial', 'send-time', 'multicast-mib', 'option-61', 'cd', 'remote-id', 'ssl-verify', 'ip-dscp-outbound', 'pki-domain-ldap', 'vc-type', 'timestamp', 'cfm', 'restart', 'ttl-mode', 'evpl', 'shaping', 'service-security', 'Pw-ve', 'sys-evpn', 'measure', 'packet-size', 'ptp', 'graceful-restart', 'ssm-policy', 'sdh-trace', 'admin-vs', 'mpls-exp-inbound', 'user-group-queue-priomode', 'rebinding-time', 'local-id-type', 'dhcpv6-server', 'radius-attribute', 'cep', 'domain-authentication-mode', 'mpls-l2vpn', 'dn-bit-set', 'is-name', 'community-list', 'set-df', 'nd', 'bras', 'FLEXE', 'user-name', 'port-tx-enabling-delay', 'session', 'admin', 'user-aging', 'rate-limit', 'best-site', 'authentication-scheme', 'lns-group', 'transmit-atm-cells', 'vlan-group-xge', 'fec-node', 'alarm-name', 'areav3', 'border-relay', 'rfc1583', 'cp-acl', 'circuit-cost', 'te-associate-group', 'unzip', 'bgp-af-vpn6', 'user-qos', 'state-refresh-interval', 'FCoE-Port', 'port-queue-alarm', 'evpn-global', 'source-port', 'mtp', 'entity-fault', 'rpf-frr', 'l2tp', '40GE', 'ap-hwtacacss', 'subscription', 'router-id', 'ecc-key-code', 'cmd', 'policy-based-route', 'source-address', 'damp-interface', 'slot-warning-threshold', 'cmu-loopback', 'prepaid-profile', 'phy-number', 'vxlan', 'aaa', 'cmu-analog', 'irb-reoriginated', 'l2-multicast-channel', 'security-password', 'e1', 'rmon-statistics', 'bye', 'xpl-rd', 'single-nexthop', 'vlan-group-ve', 'ipsec-instance', 'esp', 'telemetry', 'source', 'arp-limit', 'rate', 'bmp', 'va-lsp', 'service-class', 'user', 'renegotiation-lcp', 'request', 'apply', 'ip-attribute', 'sub-reason', 'forward-mode', 'assign', 'ripng', 'whitelist', 'least', 'atm', 'local-pw-id', 'traffic-behavior', 'domainname-parse-direction', 'client-option38', 'Global-VE', 'llid', 'multicast-profile', 'stopbits', 'Mp-group', 'cp-config', 'master', 'vpls', 'checkzero', 'header', 'hello-option', 'pki-domain-http', 'disconnect', 'probe-failtimes', 'control-word', 'isis-af-frr', 'rsa', 'igmp-snooping', 'cu-agent', 'tc-notify', 'protocol-tunnel', 'l2protocol-tunnel', 'aaa-said', 'auto-discovery', 'source-lifetime', 'hotkey', 'sec', 'loss-measure', 'deny-domain', 'fragment', 'auto-restore', 'channel-profile', 'evpl-mpls', 'ldap-server', 'debugging', 'out-interface', 'lsp-path', 'configuration-occupied', 'mac-withdraw', 'spatial-multicast', 'switch', 'auto-cost', 'redirect', 'ttl-copymode', 'link-down', 'recording-scheme', 'hwtacacs-user', 'activate-route-tag', 'ftp', 'peer-backup', 'global-gre', 'access-service', 'link-state-family', 'te-class0', 'edsg', 'write-default', 'vsi-id', 'active', 'clear', 'dampening', 'set-overload', 'accounting-service-policy', 'bgp-af-l2vpn-ad', 'netbios-type', 'ipsec-transform-esp', 'T3', 'Atm-Bundle', 'qinq', 'mld', 'destination', 'qinq-vlan', 'environment', 'pos-trunk', 'verify-source', 'community', 'ls', 'mldp-p2mp-tunnel', 'app-sec-slot', 'rpki-limit', 'keepalive', 'permit-domain', 'excluded-ip-address', 'spf-schedule-interval', 'vpn-instance-af-ipv4-mvpn', 'qppb', 'log-peer-change', 'alarm', 'login-user', 'group-name', 'frequency', 'record-interval', 'timer', 'host-packet', 'originating-rp', 'load-balance', 'statistic', 'lspv', 'probe-count', 'local-user', 'vsi', 'embedded-rp', 'avp', 'p2mp-template', 'vc', 'disable', 'bgp-af-flow-vpnv4', 'revision-level', 'accounting-scheme', 'diameter-dwr', 'terminal', 'web-server', 'period', 'te-class6', 'st', 'Global-Mp-Group', 'event', 'reflect', 'service-location', 'scramble', 'vlan-group-ge', 'Stack-Port', 'max-atm-cells', 'vpn', 'lmp-peer-te-link', 'fib-miss-report', 'echo-timeout', 'as-number', 'va-explicit-path-lsptype', 'channel', 'bfd-session', 'bgp-af-vpn-instance', 'any-address-release', 'ipv4-address', 'backward', 'crl', 'record-route', 'radius-attribute-format', 'patch', 'interface-parameter-type', 'user-warning-threshold', 'commit', 'sub-ring', 'edit', '8021p-inbound', 'script', 'weight', 'move', 'PwIf', 'netbios-name-server', 'cmu-switch', 'port-mode', 'include', 'auto-report', 'admin-template', 'pre-shared-key', 'ospfv3', 'peer', 'log', 'ipv6-prefix', 'connect-slot', 'xpl-ext-rt', 'pvc', 'icmp', 'route-tag', 'trusted-ca', 'policy', 'GigabitEthernet', '400GE', 'adjacency-strict-check', 'attack-defend', 'mask', 'syslog', 'tcp-buffer-size', 'verify', 'l2tpv3', 'mip', 'bum-suppression', 'default-user-name', 'bmp-session', 'idle-timeout', 'select-authentication-domain', 'max-sessions', 'replace', 'port-queue', 'zip', 'trap-threshold', 'tail', 'Wdm', 'local-bill-pool', 'direct-routes', 'lsp-remaining-lifetime', 'mac-address', 'y1731', 'bgp-af-ipv4-label-unicast', 'default-user-name-template', 'user-stack', 'soc', 'template', 'smart-clock', 'keep-all-routes', 'inbound', 'min-rx-interval', 'cui', 'ttl', 'reauthorize', 'using', 'read-default', 'te-class1', 'mtu-signalling', 'rm-backup-srv', '50100G', 'rpm-diagnose', 'mst-region', 'leaf-list', 'bgp-af-mdt', 'pvp', 'accounting-mode', 'put', 'protect-group', 'Remote-Ap', 'rollback', 'flag', 'undo', 'traffic', 'multi-delegate', 'pads', 'access-trigger', 'organization-unit', 'bgp-ad', 'board-channel-check', 'service-ha', 'passive', 'rsa-public-key', 'bgp-af-vpn', 'common-name', 'color', 'app-sec-interface', 'vlan-instance', 'authorization-mode', 'redirect-profile', 'multicast', 'dynamic-link-protection', 'vtracert', 'vpn-instance-af-ipv6-mvpn-ipmsi-tunnel-mpls-te', 'range', 'locality', 'lldp', 'lsp-tp', 'Trunk-Serial', 'constant-index', 'holddown-time', 'system-view', 'deterministic-med', 'more', 'pm', 'te-class5', 'site-range', 'sr-te-simulate', 'abnormal-packet-defend', 'admin-vrrp', 'mcp', 'multiplex', 'domainmaplist', 'update', 'collect', 'refresh', 'user-group-case-sensitive', 'hts-instance', 'instance', 'uninstall-module', 'c-bsr', 'ecmp', 'nhrp', 'switch-type', 'radius-no-response', 'sa', 'bi-static-transit', 'hostcar', 'mpls-hqos', 'send-router-alert', 'hash', 'protect-switch', 'reallocate-ip-address', 'pw-template', 'accounting-together', 'nqa', 'mpls-l2vpn-peer', 'sender-enable', 'remote', 'up', 'virtual-access', 'priority-car', 'acl4-ucl', 'eq', 'default-cost', 'bypass-vxlan', 'rpt-prune-delay', 'npe-upe', 'aggregation-ipv6', 'cir', 'acl-if', 'interface-match-check', 'issu', 'ip-pool', 'dumpcore', 'tdm-encapsulation-number', 'allow', 'defaultMd', 'multicast-topology6', 'arp', 'rpf-vector-path', 'blacklist', 'vsi-ldp', 'vpn-instance-af-ipv6-mvpn', 'df-election', 'mprofile', 'bind', 'anycast-rp', 'xpl', 'redirect-domain', 'vlan-mapping', 'revert', 'monitor', 'maximum-link-cost', 'route-distinguisher', 'local-backup', 'qos-local-id', 'mpls-ldp', 'role', 'mpls-l2vpn-ce', 'sys-i-evpn', 'batch-backup', 'hdlc', 'path-type', 'vsi-pwsignal-ldp-pw', 'temperature-threshold', 'compare', 'framed-ip', 'l2tpv3-pw', 'username', 'attack-source-trace', 'span', 'mprofileipv6', 'Phy-Serial', 'ip-warning-threshold', 'lsp-version', 'arp-miss', 'interface', 'delay-measure', 'source-proxy', 'client-option82', 'VBridge', 'ssl', 'gtsm', 'idle-cut', 'ipv4-flow', 'mpls', 'hwtacacs', 'rpki', 'acct-session-id-version', 'reserved', 'port-master', 'ecmp-prefer', 'pw-redundancy', 'l2-topology', 'traffic-separate', 'board', 'grpc-client', 'no-renegotiate', 'jitter-buffer', 'root', 'hierarchical-car', 'cpu-defend', 'vas-policy', '8021p', 'vlan-group-100ge', 'discriminator', 'ospf-frr', 'l2tp-group', 'xpl-ext-soo', 'hts', 'ike-identity', 'lastlistener-queryinterval', 'vsi-ipmsi-root-mldp', 'backoff', 'end-group', 'ne-number', 'cp-rate-limit', 'ldp', 'warning-threshold', 'fqdn', 'maximum', 'parse-priority', 'cdup', 'bit-error-mode', 'bsm', 'resource-template', 'tcp', 'traffic-policy-action', 'task', 'hostname', 'import-route', 'mtrace', 'domain-search-list', 'mtu-negotiate', 'unknown-frame', 'vlan-range', 'twoway-jitter', 'scp']

#strptime等不支持多线程，需要加线程锁
c = threading.RLock()

# ============================================================
# 全局 CLI 边界缓存 - 一次性预计算所有 CLI 命令边界
# Key: 文件路径绝对路径
# Value: [(start_pos, end_pos, command_text), ...] 按 start_pos 升序排列
# ============================================================
_CLI_BOUNDARY_CACHE = {}

def _normalize_cmd(cmd_text):
    """将所有 CLI 命令缩写统一归一化为标准形式
    
    dis/disp/displ/displa/display  → display
    more                           → more (不变)
    dir/sys/telnet 等              → 保持原样
    
    支持命令前有 <device> 提示符的情况，自动跳过前缀后归一化。
    """
    cmd_stripped = cmd_text.strip()
    cmd_lower = cmd_stripped.lower()
    # 匹配 dis*/display 开头（允许前面有 <device> 等提示符）
    # (?:<[^>]+>|\[[^\]]+\])? - 非捕获组：设备名提示符（可选）
    # group(1): 命令名（d/dis/.../display）
    # group(2): 参数
    m = re.match(r'^(?:<[^>]+>|\[[^\]]+\])?\s*(d(?:i(?:s(?:p(?:l(?:a)?)?)?)?)?|display)\s+([\s\S]+)$', cmd_lower)
    if m:
        # 返回归一化后的完整命令：display + 参数
        return 'display ' + m.group(2)
    return cmd_stripped


def build_cli_boundaries(diagData):
    """一次性预计算文件中所有 CLI 命令的边界位置
    
    扫描所有标准 CLI 格式的命令起点，返回排序去重后的边界列表。
    各 extract 函数通过命令关键字查表定位，O(log n) 二分查找替代 O(n) 重复正则扫描。
    
    命令归一化：dis/disp/displ/displa/display → display（便于 find_command_boundaries 前缀匹配）
    """
    if not diagData:
        return []
    
    diagData = diagData.replace('\r\n', '\n').replace('\r', '\n')
    
    # Unconditional debug: show first 5 chars to verify content
    print(f'  [BUILD-BC] CALLED, diagData len={len(diagData)}, first5={repr(diagData[:5])}')
    
    # Debug: show first 50 chars to verify correct content
    if diagData and len(diagData) > 30 and diagData[:1] == "\ufeff":
        print(f'  [FMT-DBG] build_cli_boundaries called with {len(diagData)} chars, starts with: {repr(diagData[:50])}')
    
    all_matches = []
    
    # ============================================================
    # 格式A: === 分隔符包裹 (诊断文件主流格式) - 使用行级别匹配
    #   ═══════════════════
    #                 display xxx
    #   ═══════════════════
    # 修复：逐行扫描，确保 === 分隔符不会跨行匹配
    # ============================================================
    lines = diagData.split('\n')
    line_count = len(lines)
    
    # 预计算每行的长度和累积位置（优化性能）
    line_lens = [len(l) for l in lines]
    line_starts = [0] * (line_count + 1)
    for i in range(line_count):
        line_starts[i + 1] = line_starts[i] + line_lens[i] + 1  # +1 for \n
    
    eq_line_pat = compile(r'^[^\S\n]*=+[^\S\n]*$', M)
    display_line_pat = compile(r'^[^\S\n]*(display\s+\S+(?:\s+\S+)*)[^\S\n]*$', I | M)
    
    # 快速预分类：记录所有 === 行和 display 行的索引
    eq_indices = set()
    display_info = []  # [(line_idx, start_pos, cmd_text), ...]
    
    for i in range(line_count):
        line = lines[i]
        if eq_line_pat.match(line):
            eq_indices.add(i)
        else:
            m = display_line_pat.match(line)
            if m:
                display_info.append((i, line_starts[i], m.group(1).strip()))
    
    # 组合 === display === 模式
    for disp_idx, disp_start, raw_cmd in display_info:
        # 检查前一行和后一行是否是 === 行
        if (disp_idx - 1) in eq_indices and (disp_idx + 1) in eq_indices:
            eq1_idx = disp_idx - 1
            eq2_idx = disp_idx + 1
            start_pos = line_starts[eq1_idx]
            end_line2 = line_starts[eq2_idx + 1] - 1  # -1 because last line doesn't have \n
            cmd_text = _normalize_cmd(raw_cmd)
            all_matches.append((start_pos, end_line2, cmd_text))
    
    count_by_fmt = {'fmt_A(eq+display+eq)': len(all_matches)}
    
    # ============================================================
    # 格式B: === 分隔符包裹，无右分隔符（内容从命令后开始）
    # ============================================================
    fmt_B = r'(?:^|\n)[^\S\n]*=+[^\S\n]*\n[^\S\n]*(display\s+\S+(?:\s+\S+)*)[^\S\n]*\n'
    
    # 格式Eq: ===============display clock===============\n 格式（等号包裹命令在同一行）
    # 匹配等号中间的命令内容，并计算完整的命令行边界
    # 格式：===============display clock===============\n
    # 使用捕获组：命令文本在 group(1)，边界为整个匹配范围
    # 前缀和后缀使用非捕获组
    fmt_eq_cmd = r'(?:=+\s*)(display[^\r\n=]+?)(?:=+\s*\n)'
    
    # 格式C: nicfiledata 多设备合并格式
    # <device> + =HUAWEI= + command
    # 支持两种分隔符格式：
    # 1. ============######HUAWEI#####===========（两边都有=）
    # 2. ================######HUAWEI#######（只有一边有=）
    # 命令行后面可能有换行符，也可能没有（文件末尾）
    # 注意：匹配所有命令行，不限定必须是 display 开头（任何命令都是边界）
    fmt_C1 = r'(?:^|\n)[^\S\n]*<[^>]+>[^\S\n]*\n[^\S\n]*=+######HUAWEI#####=*[^\S\n]*\n([^\n]+)(?:\n|\Z)'
    fmt_C2 = r'(?:^|\n)[^\S\n]*\[~?[^]]+\][^\S\n]*\n[^\S\n]*=+######HUAWEI#####=*[^\S\n]*\n([^\n]+)(?:\n|\Z)'
    
    # 格式D: <device>dis xxx / <device>display xxx / <device>more xxx 等同行格式
    #   只匹配真正的设备名提示符格式，如 <RR-JKBTO-01H>、<HUAWEI>、<NE8000-X16>
    #   设备名格式：字母开头，包含字母、数字、连字符、下划线、点号
    #   不匹配 <ProcId=xxx>、<Slot=x> 等非设备名格式
    # 修复：使用 [^\S\n]* 代替 \s*，避免 \s* 匹配换行符导致跨越两行匹配
    fmt_D = r'(?:^|\n)[^\S\n]*<[A-Za-z][A-Za-z0-9_\-\.]*>[^\S\n]*([^\n]+)'
    
    # 格式D2: [~device]dis xxx / [device]display xxx 等同行格式
    #   只匹配真正的设备名提示符格式，如 [~NE8000]、[JED1-PE-01]、[~HUAWEI]
    #   设备名格式：字母开头，包含字母、数字、连字符、下划线、点号，可能有 ~
    #   不匹配 [LOG-INF]、[DOPRA-xxx] 等非设备名格式
    # 修复：使用 [^\S\n]* 代替 \s*，避免 \s* 匹配换行符导致跨越两行匹配
    fmt_D2 = r'(?:^|\n)[^\S\n]*\[~?[A-Za-z][A-Za-z0-9_\-\.]*\][^\S\n]*([^\n]+)'
    
    global_patterns = [
        (fmt_B, I),
        (fmt_eq_cmd, I),  # ===============display xxx=============== 格式
        (fmt_C1, I),
        (fmt_C2, I),
        (fmt_D, I),
        (fmt_D2, I),
    ]
    
    for pattern_str, flags in global_patterns:
        fm = list(compile(pattern_str, flags).finditer(diagData))
        count_by_fmt[pattern_str[:30]] = len(fm)
        for m in fm:
            start_pos = m.start()
            end_pos = m.end()
            raw_cmd = m.group(1).strip()
            # 归一化：dis*/display/more 变体统一化
            cmd_text = _normalize_cmd(raw_cmd)
            all_matches.append((start_pos, end_pos, cmd_text))
    
    # Debug: print per-format counts (only for files containing 'AG-BDMJK' in first 1000 chars)
    if diagData and 'AG-BDMJK' in diagData[:1000]:
        print(f'  [FMT-DBG] build_cli_boundaries: {count_by_fmt}')
    
    # 去重 + 排序
    seen = set()
    unique_matches = []
    for start_pos, end_pos, cmd_text in all_matches:
        if start_pos not in seen:
            seen.add(start_pos)
            unique_matches.append((start_pos, end_pos, cmd_text))
    
    unique_matches.sort(key=lambda x: x[0])
    
    # 过滤掉非 CLI 命令的边界（如空行、纯数字行等）
    # 所有命令行都应该被识别为边界（不限定必须是 display/dis 等开头）
    # 只过滤掉明显不是命令的内容：空行、太短的单行等
    filtered = [(s, e, c) for s, e, c in unique_matches
                if (e - s) <= 1024 * 1024  # wrapper 过滤
                and len(c.strip()) >= 3]  # 命令至少3个字符
    if len(filtered) != len(unique_matches):
        print(f'Filtered out {len(unique_matches) - len(filtered)} non-CLI boundaries from boundary cache')
    return filtered


def get_cli_boundary_cache(diagFile, diagData):
    """从全局缓存中获取 CLI 边界列表，不存在则预计算并缓存"""
    abs_path = path.abspath(diagFile)
    # Debug: print first 30 chars of diagData for all files
    dbg_prefix = path.basename(diagFile) if '\\' in diagFile or '/' in diagFile else str(diagFile)
    if 'AG-BDMJK' in dbg_prefix or 'AG-BDCKJ' in dbg_prefix:
        print(f'  [BC-CACHE] {dbg_prefix}: diagData len={len(diagData) if diagData else 0}, first30={repr(diagData[:30]) if diagData else "EMPTY"}')
    if abs_path not in _CLI_BOUNDARY_CACHE:
        _CLI_BOUNDARY_CACHE[abs_path] = build_cli_boundaries(diagData)
        if _CLI_BOUNDARY_CACHE[abs_path]:
            print(f'CLI boundary cache built for {path.split(diagFile)[1]}, {len(_CLI_BOUNDARY_CACHE[abs_path])} commands found')
    else:
        if 'AG-BDMJK' in dbg_prefix or 'AG-BDCKJ' in dbg_prefix:
            print(f'  [BC-CACHE] CACHE HIT for {dbg_prefix}, {len(_CLI_BOUNDARY_CACHE[abs_path])} commands (no rebuild)')
    return _CLI_BOUNDARY_CACHE[abs_path]


def strip_cli_separator(text):
    """移除提取内容首尾的纯等号分隔符行（如 '=========' 行）"""
    # 去掉前导等号行
    text = re.sub(r'^(?:\s*=+\s*\n)+', '', text)
    # 去掉尾部等号行
    text = re.sub(r'(?:\n\s*=+\s*)+$', '', text)
    return text.strip()

def clear_cli_boundary_cache(diagFile):
    """清理指定文件的 CLI 边界缓存"""
    abs_path = path.abspath(diagFile)
    _CLI_BOUNDARY_CACHE.pop(abs_path, None)


def find_command_boundaries(cli_boundaries, target_cmd_fragment):
    """在预计算的边界列表中，查找命令文本中包含目标片段的所有边界
    
    支持归一化后的前缀匹配：
    - 搜索 'display logbuffer' 可匹配 'dis logbuffer logbuffer | no'（归一化为 'display logbuffer...'）
    - 搜索 'logfile' 可匹配 'display logfile log.log | no'
    - 搜索 'more' 可匹配 'display more startup'
    """
    results = []
    target_lower = target_cmd_fragment.lower()
    for start_pos, end_pos, cmd_text in cli_boundaries:
        # 归一化后的命令：dis*/display/more 等统一格式，前缀匹配
        cmd_lower = cmd_text.lower()
        if cmd_lower.startswith(target_lower) or target_lower in cmd_lower:
            results.append((start_pos, end_pos, cmd_text))
    return results


def find_next_boundary(cli_boundaries, current_end_pos):
    """查找当前命令之后的下一个命令边界（二分查找）"""
    left, right = 0, len(cli_boundaries) - 1
    result = None
    while left <= right:
        mid = (left + right) // 2
        start_pos = cli_boundaries[mid][0]
        if start_pos > current_end_pos:
            result = start_pos
            right = mid - 1
        else:
            left = mid + 1
    return result


def find_next_boundary_after(cli_boundaries, current_end_pos, current_start_pos):
    """查找当前命令之后的下一个命令边界，确保在 current_start_pos 之后"""
    for start_pos, end_pos, cmd_text in cli_boundaries:
        if start_pos > current_end_pos and start_pos > current_start_pos:
            return start_pos
    return None


class MyThread(threading.Thread):
    def __init__(self, func, args=()):
        super(MyThread,self).__init__()
        self.func = func
        self.args = args

    def run(self):
        self.result = self.func(*self.args)

    def get_result(self):
        try:
            return self.result  # 如果子线程不使用join方法，此处可能会报没有self.result的错误
        except Exception:
            return None
def get_content_hash(file_path):
    """计算文件内容的 MD5 哈希值（用于去重判断）"""
    try:
        import hashlib
        with open(file_path, 'rb') as f:
            # 大文件只读前8KB+后8KB做哈希，兼顾速度与准确
            hash_obj = hashlib.md5()
            f.seek(0)
            hash_obj.update(f.read(8192))
            f_size = f.seek(0, 2)
            if f_size > 16384:
                f.seek(-8192, 2)
                hash_obj.update(f.read(8192))
            elif 8192 < f_size <= 16384:
                remaining = f_size - 8192
                hash_obj.update(f.read(remaining))
            return hash_obj.hexdigest()
    except:
        return None

def are_files_duplicate(src_path, dst_path):
    """高效判断两个文件是否内容相同：先比大小，再比哈希"""
    try:
        src_size = path.getsize(src_path)
        dst_size = path.getsize(dst_path)
        if src_size != dst_size:
            return False  # 大小不同，肯定不同
    except:
        return False
    # 大小相同，再比哈希
    src_hash = get_content_hash(src_path)
    dst_hash = get_content_hash(dst_path)
    return src_hash is not None and src_hash == dst_hash

def get_content_preview(file_path, lines=5):
    """获取文件前N行的内容预览（用于比较）"""
    try:
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            preview_lines = [f.readline() for _ in range(lines)]
        return ''.join(line.strip() for line in preview_lines if line.strip())
    except:
        return ''
def is_script(data):
    temp = []
    if isinstance(data, str):
        # 配置文件不能判断为脚本
        temp = data.lower()
        if '#\nsysname ' in temp and '\n#\nreturn' in temp:
            return False
        if 'current_config' in temp or 'current config' in temp or 'current-config' in temp:
            return False
        # existing configuration 表示已有配置，不能判断为脚本
        if 'existing configuration' in temp:
            return False
        temp = temp.splitlines()
    elif isinstance(data, list):
        # 配置文件不能判断为脚本
        temp_data = '\n'.join(data).lower()
        if '#\nsysname ' in temp_data and '\n#\nreturn' in temp_data:
            return False
        if 'current_config' in temp_data or 'current config' in temp_data or 'current-config' in temp_data:
            return False
        # existing configuration 表示已有配置，不能判断为脚本
        if 'existing configuration' in temp_data:
            return False
        temp = data
    else:
        return False
    if not temp:
        return False
    # 如果字符串中包含rollback 或 roll back关键字，则认为是回退脚本，暂不处理
    #if 'rollback' in ' '.join(temp).lower() or 'roll back' in ' '.join(temp).lower():
    #    return False
    def f(x):
        if len(x)>0:
            x = x.lower()
            if x.startswith('display') or x.startswith('##') or x.startswith('*') or x.startswith('ping'):
                return ''
            elif x == '#':
                return 'return'
            elif x.startswith('undo '):
                return x.split()[1].lower()
            else:
                return x.split()[0].lower()
        else:
            return ''
    temp = list([x for x in [f(x.strip()) for x in temp] if len(x)>0])
    if len(temp) == 0:
        return False

    match_percent = float(len([x for x in temp if x in FIRST_KEYS]))/len(temp)
    #valid_percent = len(set(temp))/len(temp)
    #print(set(temp)&set(KEY_WORDS_LOWER))
    #print(temp)
    #percent = (0.6-valid_percent)*0.5 + match_percent*0.5
    #print('percent:%s', percent)
    if match_percent > 0.4:
        #print('This is a script! valid_percent:%s match_percent:%s' % (valid_percent, match_percent))
        return True
    else:
        #print('This is not a script! valid_percent:%s match_percent:%s' % (valid_percent, match_percent))
        return False
def filter_esc(s):
    if chr(27) not in s:
        return s

    item_list = s.split(chr(27))
    temp = ''
    for item in item_list:
        obj = match(r'\[(\d+)D', item)
        if obj:
            del_num = int(obj.group(1))
            if len(temp) >= int(del_num):
                temp = temp[:len(temp)-del_num] + str(item[len(obj.group(1))+2:])
            else:
                temp = str(item[len(obj.group(1))+2:])
        else:
            temp = temp + item
    return temp
def filter_bs(s):
    if chr(8) not in s:
        return s
    while chr(8) in s:
        pos = s.find(chr(8))
        sPos = 0
        ePos = 0
        if len(s)-1 > pos > 0:
            s = s[:pos]+s[pos+1:]
        elif pos == 0:
            s = s[pos+1:]
        elif pos == len(s)-1:
            s = s[:pos]
    return s
def filter_more(s):
    s = s.replace('\r\n', '\n')
    return s
def format(fileName):
    if not path.isfile(fileName):
        return False
    change_flag = False
    with open(fileName, 'rb+') as f:
        fBuff = f.read()
        temp = detect(fBuff[:5000])
        #print(temp)
        if temp['encoding'] is not None:
            fBuff = fBuff.decode(temp['encoding'], errors='ignore').splitlines()
        else:
            fBuff = fBuff.decode('UTF-8', errors='ignore').splitlines()
        data = []

        for item in fBuff:
            temp = item
            if chr(27) in item:
                change_flag = True
                temp = filter_esc(temp)
            if chr(8) in item:
                change_flag = True
                temp = filter_bs(temp)
            if '---- More ----' in item:
                change_flag = True
                temp = filter_more(temp)
            #temp = filter_esc(item)
            #temp = filter_bs(temp)
            data.append(temp)
        if change_flag:
            f.seek(0)
            f.truncate()
            f.write(bytes(('\n'.join(data)).encode('UTF-8')))
    return True
def get_kpi_info(kpiFile):
    temp = kpiFile.lower()
    if not temp.endswith(".dat") and not temp.endswith(".slog") and "kpi_" not in temp or 'logfilebuf' in kpiFile:
        return False
    endTime = ''
    regExp = search(r'(\d{4}\-\d{2}\-\d{2})\.(\d{2}\-\d{2}\-\d{2})', path.split(kpiFile)[1])
    if regExp:
        endTime = regExp.group(1) + ' ' + regExp.group(2).replace('-', ':')
    else:
        endTime = 'now'
    kpiInfo = {}
    kpiInfo.update(fileName='', neName='', fileType='', neVersion='', startTime='', endTime='', neType='')
    neName, fileType, startTime, neVersion,neType = 'UNKNOWN', 'KPI', '', '', ''

    with open(kpiFile, 'rb') as f:
        fBuf = f.read(1000)
        # license文件剔除
        if 'LicenseSerialNo' in str(fBuf):
            return False
        if len(fBuf) < 200:
            print("Unrecognized KPI format(data length<1000):", kpiFile, kpiFile)
            return False
        last_slash_index = temp.rfind('/')
        if last_slash_index == -1:
            last_slash_index = temp.rfind('\\')
        file_name = temp[last_slash_index + 1:]
        if file_name.endswith(".txt"):
            if file_name.startswith("kpi_"):
                version_type = search(r"<(\S+)>Version( ?: ?|.*?\()(.*?)(\(.*\) ?)?(V\d{3,3}R\S+?)(\)| )", str(fBuf))
                more_file = search(r"<(\S+)>(?:more|display logfile)\s+\S+\s+hex", str(fBuf))
                if more_file:
                    neName = more_file.group(1)
                neType = ''
                if version_type:
                    neName = version_type.group(1)
                    neType = version_type.group(3)
                    neVersion = version_type.group(5)
                kpiInfo.update(fileName=kpiFile, neName=neName, fileType='KPI', neVersion=neVersion,
                               startTime='2000-01-01 00:00:00', endTime=endTime, neType=neType)
                return kpiInfo
            else:
                return False

        # 如果是ATN的KPI格式 *.slog
        if kpiFile.endswith(".slog"):
            temp = detect(fBuf)
            if temp['encoding'] is not None:
                fBuf = fBuf.decode(temp['encoding'], errors='ignore')
            else:
                fBuf = fBuf.decode('UTF-8', errors='ignore')
            regExp = search(r'Sysname : (\S+)', fBuf)
            if regExp:
                neName = regExp.group(1)
            regExp = search(r'statistics at (\d{4}\-\d{2}\-\d{2} \d{2}:\d{2}:\d{2})', fBuf)
            if regExp:
                startTime = regExp.group(1)
            regExp = search(r'Version : (\S+)', fBuf)
            if regExp:
                neVersion = regExp.group(1)
            if neName == 'HUAWEI':
                neName = 'UNKNOWN'
            kpiInfo.update(fileName=kpiFile, neName=neName, fileType='KPI', neVersion=neVersion, startTime=startTime, endTime=endTime, neType='ATN950B')
            return kpiInfo

        # 如果是IPNICE数据（已禁用）
        # if 'IPNICE' in str(fBuf):
        #     data = str(fBuf)
        #     regExp = search(r'\<DevName\>(.+)\<\/DevName\>', data)
        #     if regExp:
        #         neName = regExp.group(1)
        #     regExp = search(r'<CheckStartTime>(.+)</CheckStartTime>', data)
        #     if regExp:
        #         startTime = regExp.group(1)
        #     regExp = search(r'<Version>(.+)</Version>', data)
        #     if regExp:
        #         neVersion = regExp.group(1)
        #     kpiInfo.update(fileName=kpiFile, neName=neName, fileType='IPNICE', neVersion=neVersion, startTime=startTime, endTime=endTime)
        #     return kpiInfo
        # 老KPI格式
        offset = 0
        if "KPIL.FMT" in str(fBuf[offset:offset+8]):
            frame_fhead = fBuf[8:24]
            FMT_IP1,FMT_IP2,FMT_IP3,FMT_IP4,FMT_YEAR,FMT_MONTH,FMT_DAY,FMT_H,FMT_M,FMT_S,FMT_R,FMT_LEN = unpack("!BBBBHBBBBBBI", frame_fhead)
            FMT_IP = str(FMT_IP1) + "." + str(FMT_IP2) + "." + str(FMT_IP3) + "." + str(FMT_IP4)
            startTime = str(FMT_YEAR)+"-"+str(FMT_MONTH)+"-"+str(FMT_DAY)+' '+str(FMT_H)+':'+str(FMT_M)+':'+str(FMT_S)
            neName = fBuf[24:24+FMT_LEN].decode()
            fileType = 'KPI'
            if neName == 'HUAWEI':
                neName = 'UNKNOWN'
            kpiInfo.update(fileName=kpiFile, neName=neName, fileType=fileType, neVersion=neVersion, startTime=startTime, endTime=endTime)
            return kpiInfo
        
        # 新格式：检查是否包含 \x0c 后跟设备名称的模式（AG-BDCKJ-01H KPI\lpustat.dat 格式）
        # 这种格式在 \x0c 后面跟设备名称，以 \x00 结尾
        if b'\x0c' in fBuf[:100]:
            pos = fBuf[:100].index(b'\x0c')
            # 读取 \x0c 后的数据直到 \x00
            remaining = fBuf[pos+1:pos+50]
            null_pos = remaining.find(b'\x00')
            if null_pos > 0 and null_pos < 50:
                potential_name = remaining[:null_pos].decode('utf-8', errors='ignore')
                # 验证是否是有效的设备名称（包含字母数字，长度合理）
                if potential_name and len(potential_name) >= 3 and len(potential_name) <= 32:
                    if search(r'^[A-Za-z0-9\-_]+$', potential_name):
                        neName = potential_name
                        # 尝试继续读取版本信息（\x04 后跟版本号）
                        rest_data = fBuf[pos+1+null_pos:]
                        if b'\x04' in rest_data[:50]:
                            ver_pos = rest_data.index(b'\x04')
                            ver_remaining = rest_data[ver_pos+1:ver_pos+50]
                            ver_null = ver_remaining.find(b'\x00')
                            if ver_null > 0 and ver_null < 50:
                                potential_ver = ver_remaining[:ver_null].decode('utf-8', errors='ignore')
                                if potential_ver.startswith('V') and 'R' in potential_ver:
                                    neVersion = potential_ver
                        # 尝试读取设备类型（\x05 后跟设备类型）
                        if b'\x05' in rest_data[:50]:
                            type_pos = rest_data.index(b'\x05')
                            type_remaining = rest_data[type_pos+1:type_pos+50]
                            type_null = type_remaining.find(b'\x00')
                            if type_null > 0 and type_null < 50:
                                neType = type_remaining[:type_null].decode('utf-8', errors='ignore').strip()
                        if neName == 'HUAWEI' or neName == 'Quidway':
                            neName = 'UNKNOWN'
                        kpiInfo.update(fileName=kpiFile, neName=neName, fileType='KPI', neVersion=neVersion, 
                                       startTime=startTime, endTime=endTime, neType=neType)
                        return kpiInfo
        
        # 新KPI格式
        file_flag, name_len = unpack("!LH", fBuf[0:6])
        if file_flag == 84215045:
            offset = 4
            length, = unpack("!H", fBuf[offset:offset+2])
            if not isinstance(length, int):
                print("Unrecognized KPI format(not int):", length, kpiFile)
                return False
            if length < 8:
                print("Unrecognized KPI format(header length < 8):", length, kpiFile)
                return False

            header_len = length + 12 + 4
            offset = 12
            while(offset < header_len):
                type, = unpack("!H", fBuf[offset:offset+2])
                if type == 41120:
                    break
                if type > 10:
                    print("Unrecognized KPI format(type > 10):", type, offset, kpiFile)
                    return False
                offset += 2
                data_len, = unpack("!H", fBuf[offset:offset+2])
                if not isinstance(data_len, int):
                    print("Unrecognized KPI format(not digit):", data_len, kpiFile)
                    return False
                if data_len > 1000:
                    print("Unrecognized KPI format(length exceed 1000):", data_len, kpiFile)
                    return False
                offset += 2
                data = fBuf[offset:offset+data_len]
                offset += data_len
                if type == 0:
                    neName = data.decode()
                    #print 'neName', neName
                elif type == 1:
                    continue
                elif type == 2:
                    fileType = data.decode()
                    #print 'file_type', file_type
                elif type == 3:
                    Y, M, D, H, MM, S, R = unpack("!HBBBBBB", data)
                    startTime = str(Y)+'-'+str(M)+'-'+str(D)+' '+str(H)+':'+str(MM)+':'+str(S)
                    #print 'start_time', startTime
                elif type == 4:
                    neVersion = data.decode()
                    #print 'neVersion', neVersion
            if neName == 'HUAWEI':
                neName = 'UNKNOWN'
            kpiInfo.update(fileName=kpiFile, neName=neName, fileType='KPI', neVersion=neVersion, startTime=startTime, endTime=endTime)
            return kpiInfo

        # 最老的KPI格式，没有设备名称，只有数据区，读取开始记录的时间
        offset = 0
        head_size = 10
        frame_head = fBuf[offset:offset+head_size]
        frame_date_stamp, frame_chassis, frame_slot, frame_module_id, frame_len = unpack("!LBBHH", frame_head)
        date_arr = gmtime(frame_date_stamp)
        startTime = strftime("%Y-%m-%d %H:%M:%S", date_arr)
        kpiInfo.update(fileName=kpiFile, neName=neName, fileType=fileType, neVersion=neVersion, startTime=startTime, endTime=endTime)
        return kpiInfo


def get_log_info(logFile):
    if not path.isfile(logFile):
        return False
    temp = logFile.lower()
    if not temp.endswith('.log') and not temp.endswith('.txt') and not temp.endswith('.dblg'):
        return False
    # uFAD datacollector工具采集的信息，剔除此文件
    if 'All Collect Results.txt' in logFile:
        return False
    logData = ''
    logInfo = {}
    neName, fileType, startTime, neVersion, endTime, neType = 'UNKNOWN', '', '', '', '', ''
    #防火墙日志名称2019-05-20.19-02-27.log
    timeObj = search(r'(\d{4})[\-]*(\d{2})[\-]*(\d{2})\.?(\d{2})[\-]*(\d{2})[\-]*(\d{2})', path.split(logFile)[1])
    if timeObj:
        endTime = timeObj.group(1)+'-'+timeObj.group(2)+'-'+timeObj.group(3)+' '+timeObj.group(4)+':'+timeObj.group(5)+':'+timeObj.group(6)
    else:
        endTime = 'now'

    with open(logFile, 'rb') as f:
        raw_bytes = f.read()
        # UTF-16 BOM detection: 优先于 charset_normalizer，避免误判
        bom = raw_bytes[:4]
        if bom[:2] == b'\xff\xfe':
            encoding_to_use = 'utf-16-le'
        elif bom[:2] == b'\xfe\xff':
            encoding_to_use = 'utf-16-be'
        else:
            detected = detect(raw_bytes[:5000])
            if detected['encoding'] is not None and detected['confidence'] >= 0.7:
                enc = detected['encoding'].lower()
                encoding_to_use = 'utf-16' if 'utf-16' in enc else detected['encoding']
            else:
                encoding_to_use = 'utf-8'
        logData = raw_bytes.decode(encoding_to_use, errors='ignore')

    if not logData:
        return False
    dir, fileName = path.split(temp)
    
    # 识别 nicfiledata.txt 多设备合并格式：<device> + =HUAWEI= + command
    # 这种格式包含 <device> 或 [~device] 格式的设备名
    # 提取第一个设备名作为默认网元名称，后续处理逻辑会使用
    nicfile_default_neName = None
    if '=######HUAWEI#####=' in logData[:5000]:
        nicfile_pat = search(r'^(?:<([^>]+)>|\[~?([^\]]+)\])', logData, M)
        if nicfile_pat:
            nicfile_default_neName = nicfile_pat.group(1) or nicfile_pat.group(2)
    
    if fileName.endswith('.dblg'):
        if 'This logfile is generated' not in logData[:5000]:
            return False
        # .dblg 文件是 diagnostic log，归入 DIAG 目录
        fileType = 'DIAG'
        if '\x00' in logData[:5000]:
            temp = split('[\x00\x01]+', logData[:5000])
            # 有些情况下没有记录网元名称，需要特殊判断
            #if '####' in temp[1] or '\xFE\xFF' in temp[1]:
            if '####' in temp[1]:
                neName = 'UNKNOWN'
            else:
                try:
                    neName = temp[1]
                except:
                    neName = 'UNKNOWN'
        if neName=='HUAWEI' or neName=='Quidway':
            neName = 'UNKNOWN'
        logInfo.update(fileName=logFile, neName=neName, fileType=fileType, neVersion=neVersion, startTime=startTime, endTime=endTime, neType=neType)
        return logInfo
    elif '=######HUAWEI#####=' in logData[:5000]:
        # nicfiledata.txt 多设备合并格式
        fileType = 'CMD'
        # 跳过标准日志格式检查，直接进入网元名称识别
        # （因为 nicfiledata.txt 不是标准日志格式）
        if nicfile_default_neName:
            neName = nicfile_default_neName
        # 尝试从 display version 输出中提取设备信息
        version_pat = search(r'HUAWEI\s+(\S+)\s+uptime', logData[:10000])
        if version_pat:
            neType = version_pat.group(1)
        # 尝试从 display version 输出中提取版本信息
        ver_pat = search(r'Version\s+(\S+)', logData[:10000])
        if ver_pat:
            neVersion = ver_pat.group(1)
        # 跳到提取逻辑
        dataList = []
        if neName and neName not in ['Unknown', 'UNKNOWN', '']:
            try:
                cfg_results = extract_current_configuration(logFile, logData, neName)
                if cfg_results:
                    print(f'Extracted {len(cfg_results)} CONFIG files from {path.split(logFile)[1]}')
                    dataList.extend(cfg_results)
                log_results = extract_logbuffer(logFile, logData, neName)
                if log_results:
                    print(f'Extracted {len(log_results)} LOG files from {path.split(logFile)[1]}')
                    dataList.extend(log_results)
                trap_results = extract_trapbuffer(logFile, logData, neName)
                if trap_results:
                    print(f'Extracted {len(trap_results)} trapbuffer files from {path.split(logFile)[1]}')
                    dataList.extend(trap_results)
                logfile_results, logData = extract_logfile(logFile, logData, neName)
                if logfile_results:
                    print(f'Extracted {len(logfile_results)} logfile files from {path.split(logFile)[1]}')
                    dataList.extend(logfile_results)
                more_results, logData = extract_more(logFile, logData, neName)
                if more_results:
                    print(f'Extracted {len(more_results)} more files from {path.split(logFile)[1]}')
                    dataList.extend(more_results)
                lsdb_results = extract_lsdb(logFile, logData, neName)
                if lsdb_results:
                    print(f'Extracted {len(lsdb_results)} LSDB files from {path.split(logFile)[1]}')
                    dataList.extend(lsdb_results)
                modified_data = remove_cfg_log_from_original(logData, neName)
                if modified_data != logData:
                    if not modified_data.strip():
                        try:
                            remove(logFile)
                        except:
                            pass
                    else:
                        with open(logFile, 'wb+') as f:
                            f.write(modified_data.encode('utf-8'))
                file_basename = path.split(logFile)[1]
                if file_basename.startswith('lsdb_'):
                    try:
                        remove(logFile)
                    except:
                        pass
            except Exception as e:
                print('Error extracting CONFIG/LOG/LSDB from {}: {}'.format(path.split(logFile)[1], str(e)))
        logInfo.update(fileName=logFile, neName=neName, fileType=fileType, neVersion=neVersion, startTime=startTime, endTime=endTime, neType=neType)
        dataList.insert(0, logInfo)
        return dataList
    elif 'This logfile is generated' in logData[:5000]:
        if 'diag' in fileName:
            fileType = 'DIAG'
        else:
            fileType = 'LOG'
    elif search(r'\n%?(.+? \d+:\d+:\d+).*? (\S+) (?:%%\d\d)?\w+/\d/\w+.*?:.*$', logData[-5000:].strip()) \
            and 'IKE_DEBUG' not in logData[-5000:].strip():
        if 'diag' in fileName:
            fileType = 'DIAG'
        else:
            fileType = 'LOG'
    else:
        #不识别的文件返回False
        return False
    # 按日志格式进行规则匹配，获取文件尾部日志，根据最后一条日志提取网元名称和时间
    #Nov 15 2019 17:11:24+08:00 NTJERTR1 %%01DEVM/1/hwEntityInvalid_clear(l)
    regExp = search(r'[\s\S]*\n%?([\w -]{0,30}? \d+:\d+:\d+)\S*\s*(?:DST|GMT|UTC)*\s*((?!HUAWEI).{0,80}?) (?:%%\d\d)?\w+/\d/\w+.{0,50}?:[\S\s]*?$', logData[-5000:].strip())
    if regExp:
        neName = regExp.group(2)
        endTime = get_time(regExp.group(1).split('%')[-1])
    else:
        # 如果尾部日志没有匹配到，则对全量日志进行匹配
        regExp = search(r'\n%?([\w -]{0,30}? \d+:\d+:\d+)\S*\s*(?:DST|GMT|UTC)*\s*((?!HUAWEI).{0,80}?) (?:%%\d\d)?\w+/\d/\w+.{0,50}?:', logData)
        if regExp:
            neName = regExp.group(2)
            startTime = get_time(regExp.group(1).split('%')[-1])
    if not neName:
        # 防火墙中可能会记录的是诊断命令回显信息：
        ################################################################
        #The following log information been wrote automatically
        #by logfile-timer per 5 minute(s)
        #DateTime Stamp: 2020-02-16 02:11:35.050
        # HRP_M[DH_PB02_PS_E8000_X8_FW1-diagnose]
        regExp = search(r'\[(\S+)\-diagnose\]', logData)
        if regExp:
            neName = regExp.group(1)
    if not startTime:
        regExp = search(r'%?([\w -]{0,20}? \d+:\d+:\d+).{0,100}?\S+/\d/\S+', logData)
        if regExp:
            startTime = get_time(regExp.group(1).split('%')[-1])
        else:
            regExp = search(r'DateTime Stamp: (\S+\s+\d+:\d+:\d+)', logData)
            if regExp:
                startTime = get_time(regExp.group(1))
    if 'HUAWEI' in neName or 'Quidway' in neName:
        neName = 'UNKNOWN'
    
    # 如果标准日志格式没有识别到网元名称，使用 nicfiledata.txt 格式中识别的设备名
    if neName == 'UNKNOWN' and nicfile_default_neName:
        neName = nicfile_default_neName
    
    # 新增：从命令行文件中提取CONFIG、LOG、LSDB相关内容
    # 只有在能够识别到网元名称时才进行提取
    # 返回包含主文件和生成文件的列表
    dataList = []
    if neName and neName not in ['Unknown', 'UNKNOWN', '']:
        try:
            # 提取display current-configuration内容生成CONFIG文件
            cfg_results = extract_current_configuration(logFile, logData, neName)
            if cfg_results:
                print(f'Extracted {len(cfg_results)} CONFIG files from {path.split(logFile)[1]}')
                dataList.extend(cfg_results)
            
            # 提取display logbuffer内容生成LOG文件
            log_results = extract_logbuffer(logFile, logData, neName)
            if log_results:
                print(f'Extracted {len(log_results)} LOG files from {path.split(logFile)[1]}')
                dataList.extend(log_results)
            
            # 提取display trapbuffer内容生成LOG文件
            trap_results = extract_trapbuffer(logFile, logData, neName)
            if trap_results:
                print(f'Extracted {len(trap_results)} trapbuffer files from {path.split(logFile)[1]}')
                dataList.extend(trap_results)
            
            # 提取logfile相关内容生成LOG文件（会从原始内容中删除已提取的块）
            logfile_results, logData = extract_logfile(logFile, logData, neName)
            if logfile_results:
                print(f'Extracted {len(logfile_results)} logfile files from {path.split(logFile)[1]}')
                dataList.extend(logfile_results)
            
            # 提取more相关内容生成LOG/DBLG文件（会从原始内容中删除已提取的块）
            more_results, logData = extract_more(logFile, logData, neName)
            if more_results:
                print(f'Extracted {len(more_results)} more files from {path.split(logFile)[1]}')
                dataList.extend(more_results)
            
            # 提取LSDB相关内容生成LSDB文件
            lsdb_results = extract_lsdb(logFile, logData, neName)
            if lsdb_results:
                print(f'Extracted {len(lsdb_results)} LSDB files from {path.split(logFile)[1]}')
                dataList.extend(lsdb_results)
            
            # 从原始数据中删除CONFIG和LOG回显，然后更新文件
            modified_data = remove_cfg_log_from_original(logData, neName)
            if modified_data != logData:
                # 如果修改后只剩空白内容，说明整个文件内容都被提取了，直接删除
                if not modified_data.strip():
                    try:
                        remove(logFile)
                        print(f'Removed empty file: {path.split(logFile)[1]}')
                    except:
                        pass
                else:
                    with open(logFile, 'wb+') as f:
                        f.write(modified_data.encode('utf-8'))
            
            # 如果文件名以 lsdb_ 开头，说明这个文件专门用于 LSDB 提取，提取后可以删除
            file_basename = path.split(logFile)[1]
            if file_basename.startswith('lsdb_'):
                try:
                    remove(logFile)
                except:
                    pass
        except Exception as e:
            print('Error extracting CONFIG/LOG/LSDB from {}: {}'.format(path.split(logFile)[1], str(e)))
    
    # 添加主log文件信息
    logInfo.update(fileName=logFile, neName=neName, fileType=fileType, neVersion=neVersion, startTime=startTime, endTime=endTime, neType=neType)
    dataList.insert(0, logInfo)
    
    return dataList

# 将时间转换成秒
def time2timestamp(strTime):
    if strTime.isalpha() or not strTime:
        return strTime
    #if len(strTime) != len('1970-01-01 00:00:00'):
    #    #print("para format error: " + strTime)
    #    return strTime
    oriTime = dd.strptime("1970-01-01 00:00:00", '%Y-%m-%d %H:%M:%S')
    tempTime = ''
    time_diff = ''
    if '/' in strTime:
        tempTime = dd.strptime(strTime, '%Y/%m/%d %H:%M:%S')
    elif '-' in strTime:
        tempTime = dd.strptime(strTime, '%Y-%m-%d %H:%M:%S')
    time_diff = tempTime - oriTime
    return time_diff.total_seconds()

# 格式化时间字符串1）2019-9-14 18:22:52，Nov 27 2019 15:10:35
def get_time_range(timeList):
    try:
        # timeList 格式: {fileType: {startTimestamp: endTimestamp}}
        time_range = []
        for fileType, timeDict in list(timeList.items()):
            if len(timeDict) == 0:
                continue
            # 获取最小时间戳和最大时间戳
            timestamps = sorted(timeDict.keys())
            start_time = timestamp2time(min(timestamps))
            end_time = timestamp2time(max(timeDict.values()))
            time_range.append('%s: %s - %s' % (fileType, start_time, end_time))
        return time_range
    except Exception as e:
        traceback.print_exc()
        return []


def get_time(strTime):
    strTime = strTime.strip()
    strTime = strTime.replace('  ', ' ')
    with c:
        try:
            if ':' not in strTime:
                print('Time format is wrong:', strTime)
                return strTime
            elif '-' in strTime and strTime[:4].isdigit() and strTime[5:8].isalpha():
                #2018-Dec-05 10:20:30
                return strftime('%Y-%m-%d %H:%M:%S', strptime(strTime, '%Y-%b-%d %H:%M:%S'))
            elif '-' in strTime and strTime[:4].isdigit():
                #2019-9-14 18:22:52
                return strftime('%Y-%m-%d %H:%M:%S', strptime(strTime, '%Y-%m-%d %H:%M:%S'))
            elif '-' not in strTime and strTime[:3].isalpha():
                #Nov 27 2019 15:10:35
                return strftime('%Y-%m-%d %H:%M:%S', strptime(strTime, '%b %d %Y %H:%M:%S'))
        except Exception as e:
            print("Error: get time error:", strTime)
            print(e)
    #如果时间格式不对，则返回空字符串
    print('Unknown time format:', strTime)
    return ''

def get_file_info(diagFile):
    try:
        data_info = get_kpi_info(diagFile)
        if data_info:
            return data_info
        data_info = get_log_info(diagFile)
        if data_info:
            return data_info
        data_info = get_cfg_info(diagFile)
        if data_info:
            return data_info
        # 新增：识别通过 extract_current_configuration 生成的 CONFIG 文件
        # 文件名格式：设备名_cfg.txt 或在 CONFIG/ 目录下
        data_info = get_extracted_config_info(diagFile)
        if data_info:
            return data_info
        # 新增：识别通过 extract_lsdb 生成的 LSDB 文件
        # 文件名格式：设备名_lsdb.txt 或在 LSDB/ 目录下
        data_info = get_extracted_lsdb_info(diagFile)
        if data_info:
            return data_info
        try:
            data_info = get_diag_info(diagFile)
        except Exception as e:
            print('get_diag_info Error:', e)
            print('file name:', diagFile)
            data_info = False
        if data_info:
            return data_info
        data_info = get_mop_info(diagFile)
        if data_info:
            return data_info
        data_info = get_neinfo_file(diagFile)
        if data_info:
            return data_info
        # 新增兜底：从 SNMP Trap 风格日志中提取设备名
        data_info = get_snmp_trap_log_info(diagFile)
        if data_info:
            return data_info
        return diagFile
    except Exception as e:
        print('get_file_info Error:', diagFile)
        print(e)
        return False
