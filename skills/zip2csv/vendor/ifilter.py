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

def get_snmp_trap_log_info(logFile):
    """兜底识别：从 SNMP Trap 风格日志文件中提取设备名并归类为 LOG 文件

    支持两种时间戳格式：
    格式 1: Jul 23 2020 10:13:15-05:00 Switch01 %%01MSTP/4/PORT_STATE_FORWARDING(l):...
    格式 2: 2024-11-21 02:40:57.382+07:00 AG-BDCKJ-01H %%01CPUDEFEND/7/CPCARMODIFY(D):...

    如果匹配成功，设备名从日志中间提取，返回 LOG 类型文件信息。
    """
    if not path.isfile(logFile):
        return False

    temp = logFile.lower()
    if not temp.endswith('.log') and not temp.endswith('.txt') and not temp.endswith('.dblg'):
        return False

    # uFAD datacollector 工具采集的信息，剔除此文件
    if 'All Collect Results.txt' in logFile:
        return False

    try:
        with open(logFile, 'rb') as f:
            logData = f.read()
            tempData = detect(logData[:5000])
            if tempData['encoding'] is not None:
                logData = logData.decode(tempData['encoding'], errors='ignore')
            else:
                logData = logData.decode('UTF-8', errors='ignore')

        if not logData or len(logData.strip()) == 0:
            return False

        # 格式 1: Jul 23 2020 10:13:15-05:00 DeviceName %%01...
        # 格式 2: 2024-11-21 02:40:57.382+07:00 DeviceName %%01...
        pattern_snmp = compile(
            r'(?:'
            r'[A-Z][a-z]{2}\s+\d{1,2}\s+\d{4}\s+\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}'
            r'|'
            r'\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}(?:\.\d+)?[+-]\d{2}:\d{2}'
            r')\s+(\S+)\s+%%\d\d',
            I
        )
        match = pattern_snmp.search(logData)
        if not match:
            return False

        neName = match.group(1)
        # 过滤无效设备名
        if neName in ('HUAWEI', 'Quidway', 'UNKNOWN', 'Unknown', ''):
            return False

        # 提取结束时间（取最后一条日志的时间）
        all_matches = list(pattern_snmp.finditer(logData))
        last_match = all_matches[-1]
        time_str = logData[last_match.start():last_match.start() + 50]
        endTime = 'now'
        # 尝试提取时间
        timeObj = search(r'(\d{4})[\-]*(\d{2})[\-]*(\d{2})\.?(\d{2})[\-]*(\d{2})[\-]*(\d{2})', logFile)
        if timeObj:
            endTime = timeObj.group(1) + '-' + timeObj.group(2) + '-' + timeObj.group(3) + ' ' + \
                      timeObj.group(4) + ':' + timeObj.group(5) + ':' + timeObj.group(6)

        logInfo = {
            'fileName': logFile,
            'neName': neName,
            'fileType': 'LOG',
            'neVersion': '',
            'startTime': '',
            'endTime': endTime,
            'neType': ''
        }
        return logInfo

    except Exception as e:
        print(f'get_snmp_trap_log_info Error: {logFile}, {e}')
        return False

def get_extracted_config_info(diagFile):
    """识别 extract_current_configuration 生成的 CONFIG 文件
    
    文件名格式：设备名_cfg.txt 或在 CONFIG/ 目录下
    例如：AG-BDCKJ-01H_cfg.txt 或 AG-BDCKJ-01H/CONFIG/AG-BDCKJ-01H_cfg.txt
    """
    if not path.isfile(diagFile):
        return False
    
    file_name = path.split(diagFile)[1]
    
    # 匹配格式：设备名_cfg.txt（如 AG-BDCKJ-01H_cfg.txt）
    # 设备名格式：大写字母开头，包含 -，由字母数字组成
    config_pattern = compile(r'^([A-Z][A-Z0-9]+-[A-Z0-9]+-[A-Z0-9]+)_cfg\.txt$', I)
    match = config_pattern.match(file_name)
    
    if match:
        neName = match.group(1)
        fileType = 'CONFIG'
        diagInfo = {
            'fileName': diagFile,
            'neName': neName,
            'fileType': fileType,
            'neVersion': '',
            'startTime': '',
            'endTime': '',
            'neType': ''
        }
        return diagInfo
    
    # 检查是否在 CONFIG/ 目录下
    # 目录结构应为：.../设备名/CONFIG/设备名_cfg.txt
    dir_parts = diagFile.replace('\\', '/').split('/')
    if len(dir_parts) >= 2:
        parent_dir = dir_parts[-2]  # CONFIG
        if parent_dir.upper() == 'CONFIG':
            # 从上级目录或文件名获取设备名
            grand_parent = dir_parts[-3] if len(dir_parts) >= 3 else ''
            # 设备名也可能直接编码在文件名中
            for part in dir_parts:
                config_match = config_pattern.match(part)
                if config_match:
                    neName = config_match.group(1)
                    fileType = 'CONFIG'
                    diagInfo = {
                        'fileName': diagFile,
                        'neName': neName,
                        'fileType': fileType,
                        'neVersion': '',
                        'startTime': '',
                        'endTime': '',
                        'neType': ''
                    }
                    return diagInfo
    
    return False

def get_extracted_lsdb_info(diagFile):
    """识别 extract_lsdb 生成的 LSDB 文件
    
    文件名格式：设备名_lsdb.txt 或在 LSDB/ 目录下
    例如：AG-BDCKJ-01H_lsdb.txt 或 AG-BDCKJ-01H/LSDB/AG-BDCKJ-01H_lsdb.txt
    """
    if not path.isfile(diagFile):
        return False
    
    file_name = path.split(diagFile)[1]
    
    # 匹配格式：设备名_lsdb.txt（如 AG-BDCKJ-01H_lsdb.txt）
    lsdb_pattern = compile(r'^([A-Z][A-Z0-9]+-[A-Z0-9]+-[A-Z0-9]+)_lsdb\.txt$', I)
    match = lsdb_pattern.match(file_name)
    
    if match:
        neName = match.group(1)
        fileType = 'LSDB'
        diagInfo = {
            'fileName': diagFile,
            'neName': neName,
            'fileType': fileType,
            'neVersion': '',
            'startTime': '',
            'endTime': '',
            'neType': ''
        }
        return diagInfo
    
    # 检查是否在 LSDB/ 目录下
    dir_parts = diagFile.replace('\\', '/').split('/')
    if len(dir_parts) >= 2:
        parent_dir = dir_parts[-2]  # LSDB
        if parent_dir.upper() == 'LSDB':
            for part in dir_parts:
                lsdb_match = lsdb_pattern.match(part)
                if lsdb_match:
                    neName = lsdb_match.group(1)
                    fileType = 'LSDB'
                    diagInfo = {
                        'fileName': diagFile,
                        'neName': neName,
                        'fileType': fileType,
                        'neVersion': '',
                        'startTime': '',
                        'endTime': '',
                        'neType': ''
                    }
                    return diagInfo
    
    return False
#获取info.txt文件中的网元和版本信息
def get_neinfo_file(diagFile):
    if not path.isfile(diagFile):
        return False
    if path.split(diagFile)[-1] != 'info.txt':
        return False
    diagData = ''
    diagInfo = {}
    neName, fileType, startTime, neVersion, endTime, neType = '', 'INFO', '', '', '', ''
    #print diagFile
    # UTF-16 BOM detection: 优先于 charset_normalizer 检测，避免误判
    encoding_to_use = None
    raw_bytes = None
    with open(diagFile, 'rb') as f:
        raw_bytes = f.read()
    bom = raw_bytes[:4]
    if bom[:2] == b'\xff\xfe':
        encoding_to_use = 'utf-16-le'
        print(f'  [UTF16-DETECT] BOM LE detected for {path.basename(diagFile)}, size={len(raw_bytes)}')
    elif bom[:2] == b'\xfe\xff':
        encoding_to_use = 'utf-16-be'
        print(f'  [UTF16-DETECT] BOM BE detected for {path.basename(diagFile)}, size={len(raw_bytes)}')
    if encoding_to_use is None:
        detected = detect(raw_bytes[:5000])
        if detected['encoding'] is not None and detected['confidence'] >= 0.7:
            enc = detected['encoding'].lower()
            if 'utf-16' in enc:
                encoding_to_use = 'utf-16'
            else:
                encoding_to_use = detected['encoding']
        else:
            encoding_to_use = 'utf-8'
    diagData = raw_bytes.decode(encoding_to_use, errors='ignore')
    # Verify: check if 'display current-configuration' appears in first 10KB
    if 'AG-BDMJK' in diagFile and 'diag.log' in diagFile.lower():
        first_check = diagData[:10000]
        has_cc = 'display current-configuration' in first_check
        print(f'  [UTF16-DETECT] File={path.basename(diagFile)}, enc={encoding_to_use}, size={len(raw_bytes)}, has_cc_in_first_10k={has_cc}, first_30={repr(diagData[:30])}')
    if not diagData:
        return False
    regExp = search(r'ne.name:(\S+)', diagData)
    if regExp:
        neName = regExp.group(1)
        fileType = 'INFO'
    else:
        return False

    regExp = search(r'ne.version:(\S+)', diagData)
    if regExp:
        neVersion = regExp.group(1)

    regExp = search(r'ne.type:(.+)', diagData)
    if regExp:
        neType = regExp.group(1).strip()

    diagInfo.update(fileName=diagFile, neName=neName, fileType=fileType, neVersion=neVersion, startTime=startTime, endTime=endTime, neType=neType)
    return diagInfo

def get_cfg_info(diagFile):
    if not path.isfile(diagFile):
        return False
    name, ext = get_file_prefix(diagFile)
    temp = diagFile.lower()
    if not temp.endswith('.cfg'):
        return False
    diagData = ''
    diagInfo = {}
    neName, fileType, startTime, neVersion, endTime, neType = '', '', '', '', '', ''
    #print diagFile
    # UTF-16 BOM detection: 优先于 charset_normalizer 检测，避免误判
    encoding_to_use = None
    raw_bytes = None
    with open(diagFile, 'rb') as f:
        raw_bytes = f.read()
    bom = raw_bytes[:4]
    if bom[:2] == b'\xff\xfe':
        encoding_to_use = 'utf-16-le'
        print(f'  [UTF16-DETECT] BOM LE detected for {path.basename(diagFile)}, size={len(raw_bytes)}')
    elif bom[:2] == b'\xfe\xff':
        encoding_to_use = 'utf-16-be'
        print(f'  [UTF16-DETECT] BOM BE detected for {path.basename(diagFile)}, size={len(raw_bytes)}')
    if encoding_to_use is None:
        detected = detect(raw_bytes[:5000])
        if detected['encoding'] is not None and detected['confidence'] >= 0.7:
            enc = detected['encoding'].lower()
            if 'utf-16' in enc:
                encoding_to_use = 'utf-16'
            else:
                encoding_to_use = detected['encoding']
        else:
            encoding_to_use = 'utf-8'
    diagData = raw_bytes.decode(encoding_to_use, errors='ignore')
    # Verify: check if 'display current-configuration' appears in first 10KB
    if 'AG-BDMJK' in diagFile and 'diag.log' in diagFile.lower():
        first_check = diagData[:10000]
        has_cc = 'display current-configuration' in first_check
        print(f'  [UTF16-DETECT] File={path.basename(diagFile)}, enc={encoding_to_use}, size={len(raw_bytes)}, has_cc_in_first_10k={has_cc}, first_30={repr(diagData[:30])}')
    if not diagData:
        return False
    regExp = search(r'sysname\s*(\S+)', diagData)
    if regExp:
        neName = regExp.group(1)
        fileType = 'CONFIG'
    else:
        return False
    #regExp = search(r'V\d{3}R\d{3}C\d{2}\S+', diagData)
    regExp = search(r'.ersion.+?(V\d{3}R\d{3}C\d{2}\S+)\)?\.?', diagData, M)
    if regExp:
        neVersion = regExp.group(1)
        neVersion = neVersion.strip(' )')

    diagInfo.update(fileName=diagFile, neName=neName, fileType=fileType, neVersion=neVersion, startTime=startTime, endTime=endTime, neType=neType)
    return diagInfo
def get_file_prefix(fileName):
    name, ext = path.splitext(fileName)
    #后缀名合法性校验：如果后缀名大于5或全是数字或包含数字的话并且不包含pcapng，则认为是不可识别的后缀名，算作文件名的一部分
    if (len(ext) > 5 or ext.strip('.').isdigit() or len(''.join(filter(str.isdigit, str(ext))))>1) and ('pcapng' not in ext):
        name = name+ext
        ext = ''
    return (name, ext)

# 辅助函数：去除所有空行，并统一换行符
def remove_blank_lines(content):
    """去除所有空行，统一换行符为\n"""
    if not content:
        return content
    # 统一换行符为 \n
    content = content.replace('\r\n', '\n').replace('\r', '\n')
    lines = content.split('\n')
    result = [line for line in lines if len(line.strip()) > 0]
    return '\n'.join(result)

# 辅助函数：从原始数据中删除display current-configuration和display logbuffer的回显
def remove_cfg_log_from_original(content, neName):
    """从原始数据中删除CONFIG和LOG回显，返回修改后的数据"""
    if not content:
        return content
    
    # 统一换行符
    content = content.replace('\r\n', '\n').replace('\r', '\n')
    
    result = content
    
    # 删除 display current-configuration 的回显
    # 格式1：等号分隔符格式（兼容命令头行前有空格的情况）
    cfg_pattern1 = compile(r'\n\s*=+\s*display\s+current-configuration.*?\n.*?(?=\n\s*=+\s*display\s+\S|\n<[^>]+>\s*(?:dis|quit|return|sys)|\n\[[^\]]+\]\s*(?:dis|quit|return|sys)|\Z)', S|I)
    result = cfg_pattern1.sub('', result)
    
    # 格式2：提示符格式
    cfg_pattern2 = compile(r'<{}[^>]*>\s*display\s+current-configuration\s*\n.*?(?=\n<{}[^>]*>\s*(?:dis|quit|return|sys)|\Z)'.format(re.escape(neName), re.escape(neName)), S)
    result = cfg_pattern2.sub('', result)
    
    # 格式3：HUAWEI 分隔符 + 提示符格式（nicfiledata 标准 CLI 格式）
    cfg_pattern3 = compile(rf'<{re.escape(neName)}[^>]*>\s*\n\s*=+######HUAWEI#####=+\s*\n\s*display\s+current-configuration\s*\n.*?(?=\n<{re.escape(neName)}[^>]*>\s*\n|\n\[{re.escape(neName)}\]\s*\n|\Z)', S)
    result = cfg_pattern3.sub('', result)
    
    # 带方括号设备名变体 [~设备名]
    cfg_pattern3b = compile(rf'\[~?{re.escape(neName)}[^]]*\]\s*\n\s*=+######HUAWEI#####=+\s*\n\s*display\s+current-configuration\s*\n.*?(?=\n<[^>]*>\s*\n|\n\[[^\]]*\]\s*\n|\Z)', S)
    result = cfg_pattern3b.sub('', result)
    
    # 删除 display logbuffer 的回显
    # 格式1：等号分隔符格式（兼容命令头行前有空格的情况）
    log_pattern1 = compile(r'\n\s*=+\s*display\s+logbuffer.*?\n.*?(?=\n\s*=+\s*display\s+\S|\n<[^>]+>\s*(?:dis|quit|return|sys)|\n\[[^\]]+\]\s*(?:dis|quit|return|sys)|\Z)', S|I)
    result = log_pattern1.sub('', result)
    
    # 格式2：提示符格式
    log_pattern2 = compile(r'<{}[^>]*>\s*display\s+logbuffer\s*\n.*?(?=\n<{}[^>]*>\s*(?:dis|quit|return|sys)|\Z)'.format(re.escape(neName), re.escape(neName)), S)
    result = log_pattern2.sub('', result)
    
    # 格式3：HUAWEI 分隔符 + 提示符格式（nicfiledata 标准 CLI 格式）
    log_pattern3 = compile(rf'<{re.escape(neName)}[^>]*>\s*\n\s*=+######HUAWEI#####=+\s*\n\s*display\s+logbuffer\s*\n.*?(?=\n<{re.escape(neName)}[^>]*>\s*\n|\n\[{re.escape(neName)}\]\s*\n|\Z)', S)
    result = log_pattern3.sub('', result)
    
    # 带方括号设备名变体 [~设备名]
    log_pattern3b = compile(rf'\[~?{re.escape(neName)}[^]]*\]\s*\n\s*=+######HUAWEI#####=+\s*\n\s*display\s+logbuffer\s*\n.*?(?=\n<[^>]*>\s*\n|\n\[[^\]]*\]\s*\n|\Z)', S)
    result = log_pattern3b.sub('', result)
    
    # 删除 display trapbuffer 的回显
    # 格式1：等号分隔符格式
    trap_pattern1 = compile(r'\n\s*=+\s*display\s+trapbuffer.*?\n.*?(?=\n\s*=+\s*display\s+\S|\n<[^>]+>\s*(?:dis|quit|return|sys)|\n\[[^\]]+\]\s*(?:dis|quit|return|sys)|\Z)', S|I)
    result = trap_pattern1.sub('', result)
    
    trap_pattern2 = compile(r'<{}[^>]*>\s*display\s+trapbuffer\s*\n.*?(?=\n<{}[^>]*>\s*(?:dis|quit|return|sys)|\Z)'.format(re.escape(neName), re.escape(neName)), S)
    result = trap_pattern2.sub('', result)
    
    # 删除 dis* logfile 的回显
    logfile_pattern1 = compile(r'\n\s*=+\s*dis\S+\s+logfile.*?\n.*?(?=\n\s*=+\s*display\s+\S|\n<[^>]+>\s*(?:dis|quit|return|sys)|\n\[[^\]]+\]\s*(?:dis|quit|return|sys)|\Z)', S|I)
    result = logfile_pattern1.sub('', result)
    
    logfile_pattern2 = compile(r'<{}[^>]*>\s*dis\S+\s+logfile.*?\s*\n.*?(?=\n<{}[^>]*>\s*(?:dis|quit|return|sys)|\Z)'.format(re.escape(neName), re.escape(neName)), S)
    result = logfile_pattern2.sub('', result)
    
    # 删除 more.* 的回显
    more_pattern1 = compile(r'\n\s*=+\s*more\s+.+?\n.*?(?=\n\s*=+\s*display\s+\S|\n<[^>]+>\s*(?:dis|quit|return|sys)|\n\[[^\]]+\]\s*(?:dis|quit|return|sys)|\Z)', S|I)
    result = more_pattern1.sub('', result)
    
    more_pattern2 = compile(r'<{}[^>]*>\s*more\s+.*?\n.*?(?=\n<{}[^>]*>\s*(?:dis|quit|return|sys)|\Z)'.format(re.escape(neName), re.escape(neName)), S)
    result = more_pattern2.sub('', result)
    
    # 新增：删除 LSDB 相关命令的回显（支持 display 缩写和管道参数）
    dis = r'dis(?:p(?:l(?:a(?:y)?)?)?)?'
    
    # display isis lsdb verbose
    isis_lsdb_pattern1 = compile(rf'\n\s*=+\s*{dis}\s+isis\s+lsdb\s+verbose.*?\n.*?(?=\n\s*=+\s*display\s+\S|\n<[^>]+>\s*(?:dis|quit|return|sys)|\n\[[^\]]+\]\s*(?:dis|quit|return|sys)|\Z)', S|I)
    result = isis_lsdb_pattern1.sub('', result)
    
    # 提示符格式：<设备名>disp isis lsdb verbose | no
    isis_lsdb_pattern2 = compile(rf'<{re.escape(neName)}[^>]*>\s*{dis}\s+isis\s+lsdb\s+verbose.*?\n.*?(?=\n<{re.escape(neName)}[^>]*>\s*(?:dis|quit|return|sys)|\Z)', S)
    result = isis_lsdb_pattern2.sub('', result)
    
    # 方括号格式：[设备名]disp isis lsdb verbose
    isis_lsdb_pattern3 = compile(rf'\[{re.escape(neName)}\]\s*{dis}\s+isis\s+lsdb\s+verbose.*?\n.*?(?=\n\[{re.escape(neName)}\]\s*(?:dis|quit|return|sys)|\Z)', S)
    result = isis_lsdb_pattern3.sub('', result)
    
    # display ospf lsdb router
    ospf_router_pattern1 = compile(rf'\n\s*=+\s*{dis}\s+ospf\s+lsdb\s+router.*?\n.*?(?=\n\s*=+\s*display\s+\S|\n<[^>]+>\s*(?:dis|quit|return|sys)|\n\[[^\]]+\]\s*(?:dis|quit|return|sys)|\Z)', S|I)
    result = ospf_router_pattern1.sub('', result)
    
    ospf_router_pattern2 = compile(rf'<{re.escape(neName)}[^>]*>\s*{dis}\s+ospf\s+lsdb\s+router.*?\n.*?(?=\n<{re.escape(neName)}[^>]*>\s*(?:dis|quit|return|sys)|\Z)', S)
    result = ospf_router_pattern2.sub('', result)
    
    # 方括号格式：[设备名]disp ospf lsdb router
    ospf_router_pattern3 = compile(rf'\[{re.escape(neName)}\]\s*{dis}\s+ospf\s+lsdb\s+router.*?\n.*?(?=\n\[{re.escape(neName)}\]\s*(?:dis|quit|return|sys)|\Z)', S)
    result = ospf_router_pattern3.sub('', result)
    
    # display ospf lsdb network
    ospf_network_pattern1 = compile(rf'\n\s*=+\s*{dis}\s+ospf\s+lsdb\s+network.*?\n.*?(?=\n\s*=+\s*display\s+\S|\n<[^>]+>\s*(?:dis|quit|return|sys)|\n\[[^\]]+\]\s*(?:dis|quit|return|sys)|\Z)', S|I)
    result = ospf_network_pattern1.sub('', result)
    
    ospf_network_pattern2 = compile(rf'<{re.escape(neName)}[^>]*>\s*{dis}\s+ospf\s+lsdb\s+network.*?\n.*?(?=\n<{re.escape(neName)}[^>]*>\s*(?:dis|quit|return|sys)|\Z)', S)
    result = ospf_network_pattern2.sub('', result)
    
    # 方括号格式：[设备名]disp ospf lsdb network
    ospf_network_pattern3 = compile(rf'\[{re.escape(neName)}\]\s*{dis}\s+ospf\s+lsdb\s+network.*?\n.*?(?=\n\[{re.escape(neName)}\]\s*(?:dis|quit|return|sys)|\Z)', S)
    result = ospf_network_pattern3.sub('', result)
    
    # 清理连续的空行
    result = compile(r'\n{3,}').sub('\n\n', result)
    
    return result

# 从命令行输出中提取display current-configuration内容并生成配置文件
def extract_current_configuration(diagFile, diagData, neName):
    try:
        diagData = diagData.replace('\r\n', '\n').replace('\r', '\n')
        
        # 从全局缓存中查找 display current-configuration 边界
        cli_boundaries = get_cli_boundary_cache(diagFile, diagData)
        cfg_matches = find_command_boundaries(cli_boundaries, 'display current-configuration')
        
        # 过滤1：排除带管道过滤的命令
        cfg_matches = [
            (s, e, cmd) for s, e, cmd in cfg_matches
            if not re.search(r'current-configuration\s*\|', diagData[s:e], I)
        ]
        
        # 过滤2：只保留纯 display current-configuration 命令（无额外参数）
        # 排除 display current-configuration configuration xxx / interface 等
        cfg_matches = [
            (s, e, cmd) for s, e, cmd in cfg_matches
            if cmd.lower().strip() == 'display current-configuration'
        ]
        
        if not cfg_matches:
            # 打印实际边界命令（最多前3个），排查 display current-configuration 为何未匹配
            for b in cli_boundaries[:3]:
                print(f'  DEBUG BCmd [{neName}]: {repr(b[2][:80])}')
            return []
        
        results = []
        
        for idx, (cfg_start, cfg_end, cmd_text) in enumerate(cfg_matches):
            start_pos = cfg_end
            
            # 从缓存中查找下一个命令边界（二分查找 O(log n)）
            next_pos = find_next_boundary(cli_boundaries, cfg_end)
            if next_pos is not None:
                end_pos = next_pos
            else:
                # 最后一个命令，读合理上限
                end_pos = start_pos + min(1000000, len(diagData) - start_pos)
            
            cfg_content = strip_cli_separator(diagData[start_pos:end_pos])
            
            # 过滤掉太短的内容
            if len(cfg_content) > 1000:  # 配置至少1KB
                # 创建目录（先删除可能存在的同名文件）
                config_dir = path.join(path.dirname(diagFile), 'CONFIG')
                try:
                    if path.exists(config_dir):
                        if not path.isdir(config_dir):
                            remove(config_dir)  # 删除同名文件
                            makedirs(config_dir)
                    else:
                        makedirs(config_dir)
                except FileExistsError:
                    # 多线程竞争时，目录可能已被其他线程创建
                    pass
                
                # 生成最终写入内容，用于去重对比
                cmd_title = f'<{neName}>' + ' ' + 'display current-configuration'
                final_content = cmd_title + '\n' + remove_blank_lines(cfg_content)
                
                # 内容去重：与同目录已有的 CONFIG 文件逐个对比，完全一致则跳过
                skip_write = False
                try:
                    existing_files = [f for f in listdir(config_dir) if f.startswith(f'{neName}_cfg')]
                except Exception:
                    existing_files = []
                for existing_file in existing_files:
                    existing_path = path.join(config_dir, existing_file)
                    if not path.exists(existing_path):
                        continue
                    try:
                        # 读取已有文件的原始二进制，统一换行符后比对
                        with open(existing_path, 'rb') as ef:
                            existing_bytes = ef.read()
                        existing_normalized = existing_bytes.replace(b'\r\n', b'\n').replace(b'\r', b'\n')
                        new_normalized = final_content.encode('utf-8').replace(b'\r\n', b'\n').replace(b'\r', b'\n')
                        if existing_normalized == new_normalized:
                            skip_write = True
                            break
                    except Exception:
                        continue
                if skip_write:
                    continue
                
                # 如果文件名已存在，添加序号
                cfg_file_name = f'{neName}_cfg.txt'
                cfg_file_path = path.join(config_dir, cfg_file_name)
                counter = 1
                while path.exists(cfg_file_path):
                    cfg_file_path = path.join(config_dir, f'{neName}_cfg_{counter}.txt')
                    counter += 1
                
                # 写入文件
                with open(cfg_file_path, 'w', encoding='utf-8') as f:
                    f.write(final_content)
                
                # 及时释放大字符串
                del cfg_content
                
                # 计算 zipPath
                zip_path = f'{neName}\\CONFIG\\{path.basename(cfg_file_path)}'
                
                cfg_info = {
                    'fileName': cfg_file_path,
                    'neName': neName,
                    'fileType': 'CONFIG',
                    'neVersion': '',
                    'startTime': '',
                    'endTime': '',
                    'neType': '',
                    'zipPath': zip_path
                }
                results.append(cfg_info)
        
        return results
        
    except Exception as e:
        print('extract_current_configuration error:', e)
        return []

# 从命令行输出中提取display logbuffer内容并生成日志文件
def extract_logbuffer(diagFile, diagData, neName):
    try:
        diagData = diagData.replace('\r\n', '\n').replace('\r', '\n')
        
        # 从全局缓存中查找 display logbuffer 边界
        cli_boundaries = get_cli_boundary_cache(diagFile, diagData)
        lb_matches = find_command_boundaries(cli_boundaries, 'display logbuffer')
        
        if not lb_matches:
            return []
        
        results = []
        
        for idx, (lb_start, lb_end, cmd_text) in enumerate(lb_matches):
            start_pos = lb_end
            
            # 从缓存中查找下一个命令边界（二分查找 O(log n)）
            next_pos = find_next_boundary(cli_boundaries, lb_end)
            if next_pos is not None:
                end_pos = next_pos
            else:
                end_pos = start_pos + min(1000000, len(diagData) - start_pos)
            
            log_content = strip_cli_separator(diagData[start_pos:end_pos])
            
            # 过滤掉太短的内容
            if len(log_content) > 500:
                # 创建目录（先删除可能存在的同名文件）
                log_dir = path.join(path.dirname(diagFile), 'LOG')
                try:
                    if path.exists(log_dir):
                        if not path.isdir(log_dir):
                            remove(log_dir)  # 删除同名文件
                            makedirs(log_dir)
                    else:
                        makedirs(log_dir)
                except FileExistsError:
                    # 多线程竞争时，目录可能已被其他线程创建
                    pass
                
                # 生成文件名
                log_file_name = f'{neName}_logbuffer.txt'
                log_file_path = path.join(log_dir, log_file_name)
                
                # 检查是否重复文件（前5行比较）
                if path.exists(log_file_path):
                    existing_preview = get_content_preview(log_file_path)
                    new_preview = remove_blank_lines(log_content)[:500]  # 取前500字符
                    if existing_preview and existing_preview[:200] == new_preview[:200]:
                        # 内容相同，跳过
                        del log_content
                        continue
                    # 内容不同，添加序号
                    counter = 1
                    while path.exists(log_file_path):
                        log_file_path = path.join(log_dir, f'{neName}_logbuffer_{counter}.txt')
                        counter += 1
                
                # 写入文件（去除空行，在最前面加上设备名）
                cmd_title = f'<{neName}>' + ' ' + 'display logbuffer'
                final_content = cmd_title + '\n' + remove_blank_lines(log_content)
                with open(log_file_path, 'w', encoding='utf-8') as f:
                    f.write(final_content)
                
                # 及时释放
                del log_content
                
                # 计算 zipPath
                zip_path = f'{neName}\\LOG\\{path.basename(log_file_path)}'
                
                log_info = {
                    'fileName': log_file_path,
                    'neName': neName,
                    'fileType': 'LOG',
                    'neVersion': '',
                    'startTime': '',
                    'endTime': '',
                    'neType': '',
                    'zipPath': zip_path
                }
                results.append(log_info)
        
        return results
        
    except Exception as e:
        print('extract_logbuffer error:', e)
        return []

# 从命令行输出中提取 display trapbuffer 内容并生成日志文件
def extract_trapbuffer(diagFile, diagData, neName):
    try:
        # 统一换行符格式，与 build_cli_boundaries 保持一致
        diagData = diagData.replace('\r\n', '\n').replace('\r', '\n')
        
        # 从全局缓存中查找 display trapbuffer 边界
        cli_boundaries = get_cli_boundary_cache(diagFile, diagData)
        trap_matches = find_command_boundaries(cli_boundaries, 'display trapbuffer')
        
        if not trap_matches:
            return []
        
        results = []
        all_content_parts = []
        
        for trap_start, trap_end, cmd_text in trap_matches:
            start_pos = trap_end
            
            # 从缓存中查找下一个命令边界
            next_pos = find_next_boundary(cli_boundaries, trap_end)
            if next_pos is not None:
                end_pos = next_pos
            else:
                end_pos = start_pos + min(500000, len(diagData) - start_pos)
            
            content = strip_cli_separator(diagData[start_pos:end_pos])
            
            # 过滤掉太短的内容
            if len(content) > 100:
                all_content_parts.append(content)
        
        if all_content_parts:
            # 创建目录
            log_dir = path.join(path.dirname(diagFile), 'LOG')
            try:
                if path.exists(log_dir):
                    if not path.isdir(log_dir):
                        remove(log_dir)
                        makedirs(log_dir)
                else:
                    makedirs(log_dir)
            except FileExistsError:
                # 多线程竞争时，目录可能已被其他线程创建
                pass
            
            # 生成文件名
            log_file_name = f'{neName}_trapbuffer.txt'
            log_file_path = path.join(log_dir, log_file_name)
            
            # 如果已存在，添加序号
            counter = 1
            while path.exists(log_file_path):
                log_file_path = path.join(log_dir, f'{neName}_trapbuffer_{counter}.txt')
                counter += 1
            
            # 写入文件（合并所有匹配内容）
            cmd_title = f'<{neName}>' + ' ' + 'display trapbuffer'
            final_content = cmd_title + '\n' + '\n'.join([remove_blank_lines(c) for c in all_content_parts])
            with open(log_file_path, 'w', encoding='utf-8') as f:
                f.write(final_content)
            
            # 计算 zipPath（相对于 zip 内的路径）
            zip_path = f'{neName}\\LOG\\{path.basename(log_file_path)}'
            
            log_info = {
                'fileName': log_file_path,
                'neName': neName,
                'fileType': 'LOG',
                'neVersion': '',
                'startTime': '',
                'endTime': '',
                'neType': '',
                'zipPath': zip_path
            }
            results.append(log_info)
        
        return results
        
    except Exception as e:
        print('extract_trapbuffer error:', e)
        return []

# 从命令行输出中提取 logfile 相关命令内容并生成日志文件
def extract_logfile(diagFile, diagData, neName):
    """从命令行输出中提取 logfile 相关命令内容并生成日志文件
    
    返回: (results列表, 修改后的diagData)
    """
    try:
        results = []
        diagData = diagData.replace('\r\n', '\n').replace('\r', '\n').replace('\x00\n', '\n').replace('\x00\r', '\n')
        
        # 从全局缓存中查找 logfile 相关命令
        # 命令已归一化：dis logfile → display logfile
        cli_boundaries = get_cli_boundary_cache(diagFile, diagData)
        logfile_matches = find_command_boundaries(cli_boundaries, 'display logfile')
        
        # 过滤：只保留包含 neName 的匹配（命令行提示符中有设备名，或附近日志行包含设备名）
        # 扩大前缀窗口到 200 字符，确保跨行 SSH 断开日志中的设备名也被检测到
        filtered_matches = []
        for start_pos, end_pos, cmd_text in logfile_matches:
            # 检查命令前 200 字符范围内是否有设备名提示符
            prefix_region = diagData[max(0, start_pos - 200):start_pos]
            if neName in prefix_region:
                filtered_matches.append((start_pos, end_pos, cmd_text))
        
        if not filtered_matches:
            return ([], diagData)
        
        log_parts = []
        diag_parts = []
        blocks_to_remove = []
        
        for start_pos, end_pos, cmd_text in filtered_matches:
            # 提取命令行
            cmd_line = cmd_text.strip()
            
            # 从缓存查找下一个边界（确保在当前命令之后）
            next_pos = find_next_boundary_after(cli_boundaries, end_pos, start_pos)
            
            # 检查 next_pos 是否指向真正的命令行（而不是日志内容）
            # 如果 next_pos 指向的内容不是以 <device> 或 [~device] 开头，则忽略它
            # 注意：[XXX-INF] 格式是华为设备日志的标识，不是命令行
            if next_pos is not None:
                # next_pos 可能指向行首的 \n 之后，需要找到该行的起始位置
                if diagData[next_pos] == '\n':
                    next_line_start = next_pos + 1
                else:
                    next_line_start = next_pos
                # 找到该行的结束位置
                next_line_end = diagData.find('\n', next_line_start)
                if next_line_end == -1:
                    next_line_end = len(diagData)
                next_line = diagData[next_line_start:next_line_end]
                
                # 判断是否为有效的命令行提示符：
                # 1. 以 <device> 开头（如 <RR-JKBTO-01H>）
                # 2. 以 [~device] 开头（如 [~NE8000]、[~SRG1-LE-02]）
                # 3. 不接受以 [XXX 开头且包含 -INF] 格式的日志行（如 [LOG-INF]、[DBG-INF]）
                stripped_line = next_line.strip()
                is_device_prompt = (stripped_line.startswith('<') and '>' in stripped_line[:50] and 
                                   stripped_line[1:2].isalpha())  # 设备名必须以字母开头
                # [~device] 格式的提示符，必须包含 ~ 符号
                is_sysview_prompt = (stripped_line.startswith('[') and '~' in stripped_line[:15])
                
                if not (is_device_prompt or is_sysview_prompt):
                    # next_pos 不是真正的命令行，使用文件末尾
                    next_pos = None
                # 只接受真正的命令行提示符格式（<device> 或 [~device]）
                if not (next_line.strip().startswith('<') and '>' in next_line[:50]) and \
                   not (next_line.strip().startswith('[') and '~' in next_line[:10]):
                    # next_pos 不是真正的命令行，使用文件末尾
                    next_pos = None
            
            if next_pos is not None:
                output_end_pos = next_pos
            else:
                output_end_pos = len(diagData)
            
            output_content = diagData[end_pos:output_end_pos].strip()
            full_content = cmd_line + '\n' + output_content
            blocks_to_remove.append((start_pos, output_end_pos))
            
            if len(full_content) > 10:
                # 从命令行提取文件名
                filename_from_cmd = None
                fn_match = compile(r'logfile\s+(\S+?)(?:\s*\||\s*$)', I).search(cmd_line)
                if fn_match:
                    filename_from_cmd = fn_match.group(1).strip().rstrip('|').strip()
                
                is_diag = False
                if filename_from_cmd and 'diag' in filename_from_cmd.lower():
                    is_diag = True
                elif not filename_from_cmd and ('diag.log' in output_content or compile(r'diag\S+\.zip').search(output_content)):
                    is_diag = True
                
                if is_diag:
                    diag_parts.append(full_content)
                else:
                    log_parts.append(full_content)
        
        # 从原始数据中删除已提取的块（倒序删除，保持位置正确）
        modified_data = diagData
        for start_pos, end_pos in reversed(blocks_to_remove):
            modified_data = modified_data[:start_pos] + modified_data[end_pos:]
        
        # 创建目录（LOG 和 DIAG 均写入 temp_dir/LOG/，由 copy2 按 fileType 路由到正确位置）
        log_dir = path.join(path.dirname(diagFile), 'LOG')
        try:
            if path.exists(log_dir):
                if not path.isdir(log_dir):
                    remove(log_dir)
                    makedirs(log_dir)
            else:
                makedirs(log_dir)
        except FileExistsError:
            pass
        
        # 写入 log 文件（LOG 类型 → 目标目录 LOG）
        if log_parts:
            log_file_name = f'{neName}_loglog.txt'
            log_file_path = path.join(log_dir, log_file_name)
            
            # 如果已存在，添加序号
            counter = 1
            while path.exists(log_file_path):
                log_file_path = path.join(log_dir, f'{neName}_loglog_{counter}.txt')
                counter += 1
            
            # 写入文件（每个匹配块之间用分隔符隔开），添加设备名前缀
            log_parts_with_prefix = []
            for p in log_parts:
                # 如果内容第一行已有 <neName> 前缀则保留
                lines = p.split('\n', 1)
                if lines and not lines[0].strip().startswith('<'):
                    log_parts_with_prefix.append('<' + neName + '> ' + lines[0] + ('\n' + lines[1] if len(lines) > 1 else ''))
                else:
                    log_parts_with_prefix.append(p)
            final_content = '\n\n=== SEPARATOR ===\n\n'.join(log_parts_with_prefix)
            with open(log_file_path, 'w', encoding='utf-8') as f:
                f.write(final_content)
            
            # 计算 zipPath（相对于 zip 内的路径）
            zip_path = f'{neName}\\LOG\\{path.basename(log_file_path)}'
            
            log_info = {
                'fileName': log_file_path,
                'neName': neName,
                'fileType': 'LOG',
                'neVersion': '',
                'startTime': '',
                'endTime': '',
                'neType': '',
                'zipPath': zip_path
            }
            results.append(log_info)
        
        # 写入 diag 文件 → 写入 DIAG/ 目录，fileType='DIAG'
        if diag_parts:
            diag_dir = path.join(path.dirname(diagFile), 'DIAG')
            try:
                if path.exists(diag_dir):
                    if not path.isdir(diag_dir):
                        remove(diag_dir)
                        makedirs(diag_dir)
                else:
                    makedirs(diag_dir)
            except FileExistsError:
                pass
            
            diag_file_name = f'{neName}_diaglog.txt'
            diag_file_path = path.join(diag_dir, diag_file_name)
            
            # 如果已存在，添加序号
            counter = 1
            while path.exists(diag_file_path):
                diag_file_path = path.join(log_dir, f'{neName}_diaglog_{counter}.txt')
                counter += 1
            
            # 写入文件，添加设备名前缀
            diag_parts_with_prefix = []
            for p in diag_parts:
                lines = p.split('\n', 1)
                if lines and not lines[0].strip().startswith('<'):
                    diag_parts_with_prefix.append('<' + neName + '> ' + lines[0] + ('\n' + lines[1] if len(lines) > 1 else ''))
                else:
                    diag_parts_with_prefix.append(p)
            final_content = '\n\n=== SEPARATOR ===\n\n'.join(diag_parts_with_prefix)
            with open(diag_file_path, 'w', encoding='utf-8') as f:
                f.write(final_content)
            
            # 计算 zipPath（目标是 DIAG 目录）（相对于 zip 内的路径）
            diag_zip_path = f'{neName}\\DIAG\\{path.basename(diag_file_path)}'  # 目标 DIAG 目录
            
            diag_info = {
                'fileName': diag_file_path,
                'neName': neName,
                'fileType': 'DIAG',
                'neVersion': '',
                'startTime': '',
                'endTime': '',
                'neType': '',
                'zipPath': zip_path
            }
            results.append(diag_info)
        
        return (results, modified_data)
        
    except Exception as e:
        print('extract_logfile error:', e)
        return ([], diagData)

# 从命令行输出中提取 more 相关命令内容并生成日志文件
def extract_more(diagFile, diagData, neName):
    """从命令行输出中提取 more 相关命令内容并生成日志文件
    
    返回: (results列表, 修改后的diagData)
    """
    try:
        results = []
        diagData = diagData.replace('\r\n', '\n').replace('\r', '\n').replace('\x00\n', '\n').replace('\x00\r', '\n')
        
        # 从全局缓存中查找 more 相关命令
        cli_boundaries = get_cli_boundary_cache(diagFile, diagData)
        # 精确匹配 more 命令：more filename（必须以 more 开头）
        # 不匹配 display more xxx、display diag | no-more 等含 more 但非 more 命令的情况
        more_matches = [
            b for b in cli_boundaries
            if compile(r'^(?:<[^>]+>|\[[^\]]+\])?\s*more\s+[\w\-.\:\\:]+$', I).search(b[2])
        ]
        
        if not more_matches:
            return ([], diagData)
        
        log_parts = []
        diag_parts = []
        blocks_to_remove = []
        
        for start_pos, end_pos, cmd_text in more_matches:
            cmd_line = cmd_text.strip()
            # 提取文件名：严格匹配 more xxx
            fn_match = re.match(r'^(?:<[^>]+>|\[[^\]]+\])?\s*more\s+([\w\-.\\:]+)$', cmd_line, re.I)
            if fn_match:
                filename_in_cmd = fn_match.group(1).strip()
            else:
                filename_in_cmd = ''
            
            # 查找下一个命令边界
            next_pos = find_next_boundary(cli_boundaries, end_pos)
            if next_pos is not None:
                output_end_pos = next_pos
            else:
                output_end_pos = end_pos + min(500000, len(diagData) - end_pos)
            
            content = diagData[end_pos:output_end_pos].strip()
            
            if len(content) > 100:
                cmd_title = f'<{neName}> ' + cmd_line
                full_content = cmd_title + '\n' + content
                blocks_to_remove.append((start_pos, output_end_pos))
                
                # 分类逻辑：文件名包含 diag → DIAG，否则 → LOG
                # 支持: log.log, diag.log, loglog_xx.log, diaglog_xx.log 等
                fn_lower = filename_in_cmd.lower()
                is_diag = 'diag' in fn_lower
                
                if is_diag:
                    diag_parts.append(full_content)
                else:
                    log_parts.append(full_content)
        
        # 从原始数据中删除已提取的块（倒序删除，保持位置正确）
        modified_data = diagData
        for start_pos, end_pos in reversed(blocks_to_remove):
            modified_data = modified_data[:start_pos] + modified_data[end_pos:]
        
        # 创建目录
        base_dir = path.join(path.dirname(diagFile))
        
        # 写入 log 文件到 LOG 目录
        if log_parts:
            log_dir = path.join(base_dir, 'LOG')
            try:
                if path.exists(log_dir):
                    if not path.isdir(log_dir):
                        remove(log_dir)
                        makedirs(log_dir)
                else:
                    makedirs(log_dir)
            except FileExistsError:
                pass
            
            log_file_name = f'{neName}_loglog.txt'
            log_file_path = path.join(log_dir, log_file_name)
            
            counter = 1
            while path.exists(log_file_path):
                log_file_path = path.join(log_dir, f'{neName}_loglog_{counter}.txt')
                counter += 1
            
            final_content = '\n\n=== SEPARATOR ===\n\n'.join(log_parts)
            with open(log_file_path, 'w', encoding='utf-8') as f:
                f.write(final_content)
            
            zip_path = f'{neName}\\LOG\\{path.basename(log_file_path)}'
            
            log_info = {
                'fileName': log_file_path,
                'neName': neName,
                'fileType': 'LOG',
                'neVersion': '',
                'startTime': '',
                'endTime': '',
                'neType': '',
                'zipPath': zip_path
            }
            results.append(log_info)
        
        # 写入 diag 文件到 DIAG 目录
        if diag_parts:
            diag_dir = path.join(base_dir, 'DIAG')
            try:
                if path.exists(diag_dir):
                    if not path.isdir(diag_dir):
                        remove(diag_dir)
                        makedirs(diag_dir)
                else:
                    makedirs(diag_dir)
            except FileExistsError:
                pass
            
            diag_file_name = f'{neName}_diaglog.txt'
            diag_file_path = path.join(diag_dir, diag_file_name)
            
            counter = 1
            while path.exists(diag_file_path):
                diag_file_path = path.join(diag_dir, f'{neName}_diaglog_{counter}.txt')
                counter += 1
            
            final_content = '\n\n=== SEPARATOR ===\n\n'.join(diag_parts)
            with open(diag_file_path, 'w', encoding='utf-8') as f:
                f.write(final_content)
            
            zip_path = f'{neName}\\DIAG\\{path.basename(diag_file_path)}'
            
            diag_info = {
                'fileName': diag_file_path,
                'neName': neName,
                'fileType': 'DIAG',
                'neVersion': '',
                'startTime': '',
                'endTime': '',
                'neType': '',
                'zipPath': zip_path
            }
            results.append(diag_info)
        
        return (results, modified_data)
        
    except Exception as e:
        print('extract_more error:', e)
        return ([], diagData)

# 从命令行输出中提取LSDB相关内容并生成文件
def extract_lsdb(diagFile, diagData, neName):
    try:
        # 从全局缓存中查找 LSDB 相关命令
        cli_boundaries = get_cli_boundary_cache(diagFile, diagData)
        # 严格匹配这三个 LSDB 命令行（支持 dis*/display 缩写，支持 | no / | no more 后缀）
        lsdb_exact_commands = ['display isis lsdb verbose', 'display ospf lsdb router', 'display ospf lsdb network']
        lsdb_matches = []
        for exact_cmd in lsdb_exact_commands:
            # 匹配命令：精确匹配 + 可选的后缀 | no 或 | no more
            for start_pos, end_pos, cmd_text in cli_boundaries:
                cmd_lower = cmd_text.lower().strip()
                # 检查命令是否以目标命令开头，后面可跟 | no 或 | no more
                if (cmd_lower == exact_cmd or 
                    cmd_lower.startswith(exact_cmd + ' | no') or
                    cmd_lower.startswith(exact_cmd + ' | no more')):
                    lsdb_matches.append((start_pos, end_pos, cmd_text))
        
        if not lsdb_matches:
            return []
        
        results = []
        combined_content = []
        matched_commands = []
        
        for lsdb_start, lsdb_end, cmd_text in lsdb_matches:
            start_pos = lsdb_end
            next_pos = find_next_boundary(cli_boundaries, lsdb_end)
            if next_pos is not None:
                end_pos = next_pos
            else:
                end_pos = start_pos + min(500000, len(diagData) - start_pos)
            
            lsdb_content = diagData[start_pos:end_pos].strip()
            if len(lsdb_content) > 500:
                combined_content.append(lsdb_content)
                combined_content.append('')
                matched_commands.append(cmd_text)
        
        if not combined_content:
            return []
        
        # 创建LSDB目录（先删除可能存在的同名文件）
        lsdb_dir = path.join(path.dirname(diagFile), 'LSDB')
        try:
            if path.exists(lsdb_dir) and not path.isdir(lsdb_dir):
                remove(lsdb_dir)  # 删除文件
            if not path.exists(lsdb_dir):
                makedirs(lsdb_dir)
        except FileExistsError:
            # 多线程竞争时，目录可能已被其他线程创建
            pass
        
        # 生成文件名
        lsdb_file_name = f'{neName}_lsdb.txt'
        lsdb_file_path = path.join(lsdb_dir, lsdb_file_name)
        
        # 如果已存在，添加序号
        counter = 1
        while path.exists(lsdb_file_path):
            lsdb_file_path = path.join(lsdb_dir, f'{neName}_lsdb_{counter}.txt')
            counter += 1
        
        # 写入文件（去除空行，在最前面加上设备名和实际匹配的命令）
        # 生成命令标题（使用实际匹配到的命令，用空格分隔）
        cmd_title = f'<{neName}>' + ' ' + ' '.join(matched_commands)
        final_content = cmd_title + '\n' + remove_blank_lines('\n'.join(combined_content))
        with open(lsdb_file_path, 'w', encoding='utf-8') as f:
            f.write(final_content)
        
        # 及时释放
        del combined_content
        
        # 计算 zipPath
        zip_path = f'{neName}\\LSDB\\{path.basename(lsdb_file_path)}'
        
        lsdb_info = {
            'fileName': lsdb_file_path,
            'neName': neName,
            'fileType': 'LSDB',
            'neVersion': '',
            'startTime': '',
            'endTime': '',
            'neType': '',
            'zipPath': zip_path
        }
        
        results.append(lsdb_info)
        return results
        
    except Exception as e:
        print('extract_lsdb error:', e)
        return []

def get_diag_info(diagFile):
    if not path.isfile(diagFile):
        return False
    name, ext = get_file_prefix(diagFile)
    temp = diagFile.lower()
    if not temp.endswith('.log') and not temp.endswith('.txt') and not temp.endswith('.cfg') and ext!='':
        return False
    # uFAD datacollector工具采集的信息，剔除此文件
    if 'All Collect Results.txt' in diagFile:
        return False
    # 如下是MOP文件的特征
    if (type=='RFC') and (('mop.txt' in temp) or temp.startswith('mop') or temp.startswith('rollback') or ('rollback.txt' in temp)):
        return False

    diagData = ''
    diagInfo = {}
    toolFlag = False
    neName, fileType, startTime, neVersion, endTime, neType = '', '', '', '', '', ''
    #print diagFile
    # UTF-16 BOM detection: 优先于 charset_normalizer 检测，避免误判
    encoding_to_use = None
    raw_bytes = None
    with open(diagFile, 'rb') as f:
        raw_bytes = f.read()
    bom = raw_bytes[:4]
    if bom[:2] == b'\xff\xfe':
        encoding_to_use = 'utf-16-le'
        print(f'  [UTF16-DETECT] BOM LE detected for {path.basename(diagFile)}, size={len(raw_bytes)}')
    elif bom[:2] == b'\xfe\xff':
        encoding_to_use = 'utf-16-be'
        print(f'  [UTF16-DETECT] BOM BE detected for {path.basename(diagFile)}, size={len(raw_bytes)}')
    if encoding_to_use is None:
        detected = detect(raw_bytes[:5000])
        if detected['encoding'] is not None and detected['confidence'] >= 0.7:
            enc = detected['encoding'].lower()
            if 'utf-16' in enc:
                encoding_to_use = 'utf-16'
            else:
                encoding_to_use = detected['encoding']
        else:
            encoding_to_use = 'utf-8'
    diagData = raw_bytes.decode(encoding_to_use, errors='ignore')
    # Verify: check if 'display current-configuration' appears in first 10KB
    if 'AG-BDMJK' in diagFile and 'diag.log' in diagFile.lower():
        first_check = diagData[:10000]
        has_cc = 'display current-configuration' in first_check
        print(f'  [UTF16-DETECT] File={path.basename(diagFile)}, enc={encoding_to_use}, size={len(raw_bytes)}, has_cc_in_first_10k={has_cc}, first_30={repr(diagData[:30])}')
    if not diagData:
        return False
    # Macintosh 字符格式处理
    if '\r\r\n' in diagData[:2000]:
        diagData = diagData.replace('\r\r\n', '\r\n')
        with open(diagFile, 'wb+') as f:
            print('Warning: the format of the file is 0D0D0A')
            f.write(diagData.encode())
    #regExp = search(r'<(.+)>\s*dis\S+\s*diag', diagData, I)
    # filter_str用于标识终端追加的特殊字符，如：13:51:16:227 ||<E8000X16-QUI01-ISP-FW-01>display cpu-usa
    filter_str = ''
    regExp = search(r'(?:\n|^)([\w\d :\.§$\-_\[\]]{0,40}?)(?:HRP_M|HRP_S|HRP_A)?<(.+)>\s*dis\S*\s*diag\S*\s*(?:\n|$)', diagData, I)
    if regExp:
        filter_str = regExp.group(1)
        neName = regExp.group(2)
        fileType = 'CMD'  # dis diag 命令输出归入 CMD
    else:
        # 先用 build_cli_boundaries 检测文件是否包含 CLI 命令分隔符格式
        # 如果包含，说明是标准的诊断日志或命令行文件，应该归类为 CMD
        try:
            cli_boundaries = build_cli_boundaries(diagData)
            if cli_boundaries and len(cli_boundaries) >= 10:
                fileType = 'CMD'
        except:
            pass
        
        # 如果 CLI 边界检测未命中，再用传统方式检测
        if fileType == '':
            regExp = search(r'=============display|\n                 \s*display version', diagData)
            if regExp:
                fileType = 'DIAG'
    
    # ---- LOG 检测：dis logfile / display logfile 命令（RR-JKBTO-01H.log 格式）----
    #   <device>dis logfile log.log | no
    #   <device>dis logfile diag.log | no
    # 这类文件虽然包含 dis/disp 命令，但本质是日志文件，不应归为普通 CMD
    if fileType == '':
        logfile_regExp = search(r'<[^>]+>\s*(?:dis(?:p(?:l(?:a)?)?)?|display)\s+logfile\s+\S+', diagData, I)
        if logfile_regExp:
            fileType = 'LOG'
    
    # 先从配置中获取网元名称，sysname JED1-PE-01
    # 如果获取不到则从命令行用户视图中提取：<JED1-PE-01>dis
    # 如果获取不到则从命令行系统视图中提取：[~JED1-PE-01]dis, 注意，系统视图的中可能包含子视图，可能不准确
    # 如果文件名包含设备名（如 lsdb_AG-BDCKJ-01H.txt），则从文件名中提取

    if neName == '':
        # 尝试从文件名中提取设备名（处理 lsdb_XXX.txt 等格式）
        file_name = path.split(diagFile)[1]
        name_match = search(r'(?:^|_)([A-Z][A-Z0-9]+-[A-Z0-9]+-[A-Z0-9]+)(?:_|\.)', file_name, I)
        if name_match:
            neName = name_match.group(1)
        
        if neName == '' and fileType == '':
            fileType = 'CMD'
        
        # 新增：如果文件使用等号分隔符格式，从 display current-configuration 回显中提取 sysname
        # 这种格式没有 <设备名> 提示符，设备名在配置内容中
        if fileType == 'DIAG' and neName == '':
            # 匹配等号分隔符格式的 display current-configuration
            cfg_pattern = compile(r'=+\s*display\s+current-configuration\s*=+\s*(?:---[\s\S]+?---)?', I)
            cfg_match = cfg_pattern.search(diagData)
            if cfg_match:
                # 从匹配位置后开始搜索 sysname（最多搜索50万字符）
                cfg_content = diagData[cfg_match.end():cfg_match.end() + 500000]
                # 匹配 sysname，支持 Unix (\n) 和 Windows (\r\n) 换行符
                regExp = search(r'^\s*sysname\s+(\S+)', cfg_content, M)
                if regExp:
                    neName = regExp.group(1)
                    print(f'Extracted neName "{neName}" from display current-configuration in {path.split(diagFile)[1]}')
        
        #regExp = search(r'(?:\n|^)([\d :\-_]{0,30}?) *sysname\s*(.+)\r', diagData, M)
        # 修改：支持 Unix (\n) 和 Windows (\r\n) 换行符
        regExp = search(r'(?:\n|^)([\w\d :\.§$\-_\[\]]{0,40}? *)sysname\s(\S.+\S)\r?\n?', diagData, M)
        if regExp:
            filter_str = regExp.group(1)
            neName = regExp.group(2)
            #如果是巡检采集的信息，文件名和第一行都是采集命令行
            first_line = diagData.splitlines()[0]
            if (first_line in diagFile or first_line=='display current-configuration' or first_line=='display saved-configuration') and first_line.strip():
                toolFlag = True
                with open(diagFile, 'wb+') as f:
                    temp_data = '<'+neName+'>'+first_line+diagData[len(first_line):]
                    f.write(temp_data.encode())
            # 规避产品命令行中存在display current-configuration存在简写的问题，下个补丁解决后删除--- 20210205
            # temp_data = sub('\s*dis\S*\s+cu\S*', 'display current-configuration', first_line)
            # if temp_data != first_line:
            #     with open(diagFile, 'wb+') as f:
            #         temp_data = temp_data+diagData[len(first_line):]
            #         f.write(temp_data.encode())
        else:
            #regExp = search(r'^<(\S+)>\s*(dis|more|dir)', diagData, M)
            regExp = findall(r'(?:\n|^)([\w\d :\.§$\-_\[\]]{0,40}?)(?:HRP_M|HRP_S|HRP_A)?<(\S+)>\s*(?:dis|more|dir|sys|telnet)', diagData, S|I)
            if regExp:
                #neName = regExp.group(1)
                filter_str = regExp[-1][0]
                # 获取出现频次最大的作为网元名称
                temp_list = [x[1] for x in regExp]
                neName = Counter(temp_list).most_common(1)[0][0]
            else:
                regExp = search(r'(?:\n|^)([\w\d :\.§$\-_\[\]]{0,40}?)(?:HRP_M|HRP_S|HRP_A)?\[~?(\S+?)(?:-aaa|-diagnose|-interface|-ui-vty0)?\]\s*dis', diagData, M|I)
                if regExp:
                    filter_str = regExp.group(1)
                    neName = regExp.group(2)
                else:
                    #如果是巡检采集的信息，文件名和第一行都是采集命令行
                    first_line = diagData.splitlines()[0]
                    #first_line = first_line.split(">")[-1].strip()
                    base_name = path.basename(get_file_prefix(diagFile)[0])
                    #如果文件的第一行包含文件名，或者文件名包含文件的第一行，则认为是工具自动采集的数据
                    #工具采集文件：baq-lvi-s9303-01.interface
                    first_line_string = ''.join(first_line.split(' '))
                    file_name_tag = base_name.split('.')[-1]
                    if (first_line in diagFile
                        or base_name in first_line
                        or ('.version' in diagFile and 'display version' in first_line)
                        or file_name_tag in first_line_string) and first_line.strip():
                        toolFlag = True
                        regExp = search(r'^<((?!ProcId|FEI)\S+)>', diagData, M)
                        if regExp:
                            neName = regExp.group(1)
                            with open(diagFile, 'w+') as f:
                                temp_data = '<'+neName+'>'+first_line+diagData[len(first_line):]
                                f.write(temp_data)
                        else:
                            neName = 'Unknown'
                    else:
                        print('Error:No Display command was found in CMD file:%s' % path.split(diagFile)[1])
                        return False
    filter_str = filter_str.replace('HRP_S', '')
    filter_str = filter_str.replace('HRP_M', '')
    filter_str = filter_str.replace('HRP_A', '')
    filter_str = filter_str.replace('HRP_B', '')
    # 暂时先不过滤特殊字符了，前面追加的字符中可能包含特殊字符
    #filter_str = ''.join(filter(lambda x: ord(x)>=ord(' ') and ord(x)<=ord('~'), filter_str)) #过滤掉特殊字符
    if filter_str and filter_str.strip()!='':
        if 'more' in filter_str.lower():
            diagData = diagData.replace(filter_str, '')
        else:
            print('【filter string】[file]%s[str]%s[ASCII]%s' % (path.split(diagFile)[1], filter_str, '-'.join(list([str(ord(x)) for x in filter_str]))))
            diagData = sub(r'(?:\n|^).{%s}' % len(filter_str), '\n', diagData)
        with open(diagFile, 'wb+') as f:
            f.write(diagData.encode())

    # 优先级最高：从 'HUAWEI xxx uptime is' 行提取 neType
    # 示例：
    #   HUAWEI CE6863-48S6CQ uptime is 9 days, 5 hours, 49 minutes
    #   HUAWEI NetEngine 8000 M14 uptime is 946 days, 14 hours, 23 minutes
    #   HUAWEI MultiserviceEngine 60-X16 uptime is 4533 days, 3 hours, 43 minutes
    huawei_uptime_pattern = compile(r'\bHUAWEI\s+(.*?)\s+Uptime\s+is\b', I)
    huawei_uptime_match = search(huawei_uptime_pattern, diagData)
    if huawei_uptime_match:
        neType = huawei_uptime_match.group(1).strip()

    # 版本提取逻辑始终执行（不依赖 neType 是否为空）
    pattern = compile(r'(?<!")dis\S+\s+ver.{0,400}?oftware.{0,20}?ersion.{0,50}?\s*\(?((?:ATN|NetEngine|AP\S+)?\s*\S+)\s*(V\d{3}R\d{3}C\d{2}\w*)\)?\.?', S|I)
    regExp = search(pattern, diagData)
    if regExp:
        neVersion = regExp.group(2).strip(' )')
        # 仅在旧逻辑未获取到 neType 时才用旧正则的结果覆盖
        if not neType:
            old_neType = regExp.group(1)
            neType = old_neType.strip('(').strip()
    else:
        # 尝试 vrbd 模式提取版本+型号
        pattern = compile(r'(?<!")vrbd.{0,400}?\n(.{0,20}?) Version (V\d{3}R\d{3}C\d{2}\w*).', S|I)
        regExp = search(pattern, diagData)
        if regExp:
            neVersion = regExp.group(2)
            if not neType:
                neType = regExp.group(1).strip()
        else:
            # 尝试从配置注释中提取版本
            regExp = search(r'^!Software Version (V\d{3}R\d{3}C\d{2}\w*)', diagData, M)
            if regExp:
                neVersion = regExp.group(1)

    # 兜底：如果还是没有 neType，尝试从 display device 中提取
    if not neType:
        pattern = compile(r'(?<!")display device.{0,400}?\n(.{0,25}?)\'s Device status', S|I)
        regExp = search(pattern, diagData)
        if regExp:
            neType = regExp.group(1)
            neType = neType.strip()

    pattern = compile(r'display clock.+?(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})', S)
    regExp = search(pattern, diagData)
    if regExp:
        startTime = regExp.group(1)
    # 规避产品命令行中存在双引号的问题，下个补丁解决后删除--- 20210129
    # if fileType == 'DIAG' and neType.startswith("CE"):
    #     print('>>>>>>>>>>>>>>>>>>>>>>>>>')
    #     diagData = diagData.replace('display forward information slot', 'werdsadfwr display forward information slot')
    #     with open(diagFile, 'wb+') as f:
    #         f.write(bytes(diagData.encode('UTF-8')))
    # 规避防火墙IPSEC debug信息--- 20210428
    if neType.startswith("Eud") and 'IKE/7/IKE_DEBUG' in diagData:
        print('>>>>>>>>>>>>>>>>>>>>>>>>>')
        ike_debug = []
        ike_debug.append('<'+ neName + '>display ikev2 filter remote-address x.x.x.x')
        for line in diagData.splitlines():
            if 'IKE/7/IKE_DEBUG' in line:
                ike_debug.append(line)
        diagData = diagData + '\n' + '\n'.join(ike_debug)
        with open(diagFile, 'wb+') as f:
             f.write(bytes(diagData.encode('UTF-8')))
    # neType 保留原始空格（如 "NetEngine 8000 M8"），不再执行 replace(' ', '')
    
    # 新增：从命令行文件中提取CONFIG、LOG、LSDB相关内容
    # 只有在能够识别到网元名称时才进行提取
    # 返回包含主文件和生成文件的列表
    dataList = []
    if neName and neName not in ['Unknown', 'UNKNOWN', '']:
        try:
            # 全局 CLI 边界缓存预计算（一次性扫描，各 extract 函数查表使用）
            cli_boundaries = get_cli_boundary_cache(diagFile, diagData)
            
            # 提取display current-configuration内容生成CONFIG文件
            cfg_results = extract_current_configuration(diagFile, diagData, neName)
            if cfg_results:
                print(f'Extracted {len(cfg_results)} CONFIG files from {path.split(diagFile)[1]}')
                dataList.extend(cfg_results)
            
            # 提取display logbuffer内容生成LOG文件
            log_results = extract_logbuffer(diagFile, diagData, neName)
            if log_results:
                print(f'Extracted {len(log_results)} LOG files from {path.split(diagFile)[1]}')
                dataList.extend(log_results)
            
            # 提取display trapbuffer内容生成LOG文件
            trap_results = extract_trapbuffer(diagFile, diagData, neName)
            if trap_results:
                print(f'Extracted {len(trap_results)} trapbuffer files from {path.split(diagFile)[1]}')
                dataList.extend(trap_results)
            
            # 提取logfile相关内容生成LOG文件（会从原始内容中删除已提取的块）
            logfile_results, diagData = extract_logfile(diagFile, diagData, neName)
            if logfile_results:
                print(f'Extracted {len(logfile_results)} logfile files from {path.split(diagFile)[1]}')
                dataList.extend(logfile_results)
            
            # 提取more相关内容生成LOG/DBLG文件（会从原始内容中删除已提取的块）
            more_results, diagData = extract_more(diagFile, diagData, neName)
            if more_results:
                print(f'Extracted {len(more_results)} more files from {path.split(diagFile)[1]}')
                dataList.extend(more_results)
            
            # 提取LSDB相关内容生成LSDB文件
            lsdb_results = extract_lsdb(diagFile, diagData, neName)
            if lsdb_results:
                print(f'Extracted {len(lsdb_results)} LSDB files from {path.split(diagFile)[1]}')
                dataList.extend(lsdb_results)
            
            # 从原始数据中删除CONFIG和LOG回显，然后更新文件
            modified_data = remove_cfg_log_from_original(diagData, neName)
            if modified_data != diagData:
                # 如果修改后只剩空白内容，说明整个文件内容都被提取了，直接删除
                if not modified_data.strip():
                    try:
                        remove(diagFile)
                        print(f'Removed empty file: {path.split(diagFile)[1]}')
                    except:
                        pass
                else:
                    with open(diagFile, 'wb+') as f:
                        f.write(modified_data.encode('utf-8'))
            
            # 如果文件名以 lsdb_ 开头，说明这个文件专门用于 LSDB 提取，提取后可以删除
            file_basename = path.split(diagFile)[1]
            if file_basename.startswith('lsdb_'):
                try:
                    remove(diagFile)
                except:
                    pass
        except Exception as e:
            print('Error extracting CONFIG/LOG/LSDB from {}: {}'.format(path.split(diagFile)[1], str(e)))
    
    # 添加主diag文件信息
    diagInfo.update(fileName=diagFile, neName=neName, fileType=fileType, neVersion=neVersion, startTime=startTime, endTime=endTime, neType=neType)
    dataList.insert(0, diagInfo)
    
    # 清理当前文件的 CLI 边界缓存，防止内存泄漏
    clear_cli_boundary_cache(diagFile)
    
    return dataList

def get_mop_info(fileName):
    if type != 'RFC':
        return False
    if not path.isfile(fileName):
        return False
    name, ext = get_file_prefix(fileName)
    temp = path.split(fileName)[1].lower()
    txt_ret = get_mop_txt(fileName)

    if not ('mop.txt' in temp) and not ('mop.csv' in temp) \
       and not temp.startswith('mop') and not temp.startswith('rollback') \
       and not ('rollback.txt' in temp) and not ('rollback.csv' in temp) and not temp.endswith('.xlsx') \
       and not txt_ret:
        return False
    mopData = ''
    diagInfo = {}
    dataList = []
    neName, fileType, startTime, neVersion, endTime, neType = 'MOP', 'MOP', '', '', '', ''
    #如果是.xlsx文件，则可能是操作脚本文件，需要从中提取脚本信息并转换成txt文件，后续流程会对该文件进行分析，生成csv文件
    if temp.endswith('.xlsx'):
        script, rollback = get_mop_xls(fileName)
        #print('script length:', len(script), len(rollback))
        if len(script) > 0:
            script_file = name+'_mop.txt'
            with open(script_file, 'wb+') as f:
                f.write(script.encode())
            diagInfo.update(fileName=script_file, neName=neName, fileType=fileType, neVersion=neVersion, startTime=startTime, endTime=endTime, neType=neType)
            dataList.append(diagInfo.copy())
        if len(rollback) > 0:
            rollback_file = name+'_rollback.txt'
            with open(rollback_file, 'wb+') as f:
                f.write(rollback.encode())
            diagInfo.update(fileName=rollback_file, neName=neName, fileType='Rollback', neVersion=neVersion, startTime=startTime, endTime=endTime, neType=neType)
            dataList.append(diagInfo.copy())
        if dataList:
            return dataList
        else:
            return False
    if temp.endswith('rollback.txt') or temp.endswith('rollback.csv') or temp.startswith('rollback'):
        fileType = 'Rollback'
    elif 'mop.txt' in temp or 'mop.csv' in temp or temp.startswith('mop'):
        fileType = 'MOP'
    elif txt_ret:
        fileType = txt_ret
    
    diagInfo.update(fileName=fileName, neName=neName, fileType=fileType, neVersion=neVersion, startTime=startTime, endTime=endTime, neType=neType)
    return diagInfo

# 根据文件内容判断txt文件是否为操作脚本或回退脚本：
# 如果不是则返回空字符串
# 如果是操作脚本，则返回'Script'
# 如果是回退脚本，则返回'Rollback'
# 如果文件中既包含操作脚本，也包含回退脚本，则返回'MOP'
def get_mop_txt(fileName):
    if not fileName.endswith('.txt'):
        return False
    data = ''
    display_flag = False
    script_flag = False
    rollback_flag = False
    f_name = path.split(fileName)[1].lower()
    if 'rollback point' in f_name:
        script_flag = True
        return 'MOP'
    if 'rollback' in f_name or 'roll back' in f_name or 'changeback' in f_name:
        #如果文件名中存在rollback关键字，则为回退脚本
        rollback_flag = True
        return 'Rollback'
    elif 'script' in f_name or 'operation' in f_name:
        #否则如果包含script关键字，则为操作脚本
        script_flag = True
        return 'MOP'

    with open(fileName, 'rb') as f:
        data = f.read()
        tempData = detect(data[:5000])
        #print(temp)
        if tempData['encoding'] is not None:
            data = data.decode(tempData['encoding'], errors='ignore')
        else:
            data = data.decode('UTF-8', errors='ignore')
        #data = f.read()[:5000].lower()
    if '>dis' in data or ']dis' in data or 'display ' in data:
        display_flag = True
    if 'rollback' in data or 'roll back' in data or 'changeback' in data and 'rollback point' not in data:
        rollback_flag = True
        data = data.replace('back script', '')
    if ' script' in data:
        script_flag = True

    if not display_flag:
        # 在变更场景，如果存在不包含display命令行的txt文件，只能是脚本，后续如果有误识别，再单独处理
        if script_flag and rollback_flag:
            return 'Rollback'
        elif rollback_flag:
            return 'Rollback'
        elif script_flag:
            return 'MOP'
        else:
            return ''
    else:
        # 如果包含display命令行，可能是采集的设备信息，存在误识别的可能，后续针对具体情况再处理
        if script_flag and rollback_flag:
            return 'Rollback'
        elif rollback_flag:
            return 'Rollback'
        elif script_flag:
            return 'MOP'
        else:
            return ''
def get_device_name_column(columns):

    r = list(filter(lambda x:re.search("^Device *Name$", x.strip(),re.IGNORECASE), columns))

    if len(r) == 1:
        return r[0]

def get_device_df1(df:pd.DataFrame):
    out = {}

    trans_table = str.maketrans("", "", "\",!@[]*/|:<>?")

    for c,series in df.items():
        nename = ''
        tmp = []
        for i in series:
            t = re.search(r"^((?!\*|\?|\[|\]|<|>|\\|/|\"|,|!|@)[ \S]+)\((?:\d+\.\d+\.\d+\.\d+|[\da-fA-F:]+)\)$",str(i).strip())
            if t:
                if tmp and nename:
                    out[nename] = pd.DataFrame(pd.Series(tmp),columns=[nename])

                nename = t.groups()[0]

                tmp= []
                continue

            tmp.append(i)

        if tmp and nename:
            out[nename] = pd.DataFrame(pd.Series(tmp), columns=[nename])

    return out



def get_device_df(item, max_columns = 100):

    device_name_column = get_device_name_column(list(item.columns[:max_columns]))

    if device_name_column is not None:
        item[device_name_column].fillna(method="ffill", inplace=True)
        dfg = item.groupby(device_name_column)
        return dict(list(dfg))
    tmp = get_device_df1(pd.concat([pd.DataFrame([item.columns], columns=item.columns), item]).iloc[:,:100])
    if tmp:
        return tmp
    return {"":item}


def get_mop_xls(fileName):
    #print('Processing xls MOP file:')
    script_list = []
    rollback_list = []
    isTables = fileName.endswith('tables.xlsx')
    if isTables:
        sheettitle={}
        df = pd.read_excel(fileName, sheet_name=None)
        for sheet, item in list(df.items()):
            sheettitle[sheet] = item.columns[0]

        df = pd.read_excel(fileName, sheet_name=None,skiprows=1)
    else:
        # 读取100列，后面判断，如果超过100列，则说明脚本有问题
        df = pd.read_excel(fileName, sheet_name=None, usecols=[x for x in range(100)])
    #print(len(df), fileName)
    for sheet, item in list(df.items()):
        #df_script = df[item].copy()
        #df[item].fillna('', inplace=True)
        #如果sheet页命名中包含rollback，则认为是回退脚本
        #print('#####Sheet:', sheet, sheet.lower())
        if len(item.columns)>=100:
            print('Warning: The column count in file [%s] sheet [%s] exceeds 100!' % (path.split(fileName)[-1], sheet))
        tabletitle = ''
        if isTables:
            tabletitle = sheet.lower() + " " + sheettitle[sheet].lower()
        else:
            tabletitle = sheet.lower()
        #如果sheet页名字中包含如下字段，则认为是回退脚本
        if any([x in tabletitle for x in ['rollback','roll back','changeback','change back', 'recover']]):
            rollback_list_temp = []

            device_dfs = get_device_df(item, 100)
            for i, device_df in device_dfs.items():
                if i:
                    rollback_list_temp += ["####nename: " + i.strip()]
                    script_list_temp += ["####nename: " + i.strip()]

                for index in device_df.columns[:100]:
                    temp = list(device_df[index].dropna())

                    temp = [i for i in temp if isinstance(i, str)]

                    if len(temp)>0:
                        #一个单元格中可能有多条命令，需要进行拆分
                        temp = reduce(lambda x,y:x+y, [i.splitlines() for i in temp])
                    # 如果脚本中存在如下命令rollback configuration to label，说明是回退脚本
                    temp = [str(index)] + temp
                    if is_script(temp) or 'rollback configuration to label' in ' '.join(temp).lower():
                        rollback_list_temp = rollback_list_temp + ["####column_title: " + str(index)] + temp
            if rollback_list_temp:
                rollback_list = rollback_list + ["####sheet_title: " + str(sheet)] + rollback_list_temp
        else:
            script_list_temp = []
            rollback_list_temp = []
            device_dfs = get_device_df(item, 100)
            for i, device_df in device_dfs.items():
                if i:
                    rollback_list_temp += ["####nename: " + i.strip()]
                    script_list_temp += ["####nename: " + i.strip()]
                for index in device_df.columns[:100]:
                    temp = list(device_df[index].dropna())
                    temp = [i for i in temp if isinstance(i, str)]
                    if len(temp)>0:
                        temp = reduce(lambda x,y:x+y, [i.splitlines() for i in temp])
                    temp = [str(index)] + temp
                    if is_script(temp):
                        s = index.lower() + "\n"+ '\n'.join(temp).lower().replace('rollback label', '')
                        # 如果有设置回退标签，说明是操作脚本
                        reg = search(r'commit\s+label\s*\S+', s)
                        if any([x in s for x in ['rollback','roll back','changeback','change back','recover']]) and reg is None:
                            rollback_list_temp = rollback_list_temp + ["####column_title: " + str(index)] + temp
                        else:
                            script_list_temp = script_list_temp + ["####column_title: " + str(index)] + temp


            if script_list_temp:
                script_list = script_list + ["####sheet_title: " + str(sheet)] + script_list_temp
            if rollback_list_temp:
                rollback_list = rollback_list + ["####sheet_title: " + str(sheet)] + rollback_list_temp

    #df_script = map(lambda x:str(x), df_script)
    script_list = [i for i in script_list if i!='' and isinstance(i, str)]
    script = '\n'.join(script_list)
    rollback_list = [i for i in rollback_list if i!='' and isinstance(i, str)]
    rollback = '\n'.join(rollback_list)
    return script, rollback
def pre_check_kpi_dir(file_list):
    for root, dirs, files in file_list:
        root_path = root
        if not root.endswith(path.sep):
            root_path = root + path.sep
        for file in files:
            pre_check_kpi(file, root_path)


def pre_check_kpi(file, root_path):
    if file.endswith('.txt') or file.endswith('.log') or not "." in file:
        is_text, encoding = is_text_file(root_path + file)
        if is_text:
            content = read_file_with_encoding(root_path + file, encoding)
            is_more_file = search(r"(<\S+>)(?:more|display logfile)\s+\S+\s+hex", content, I|M)
            if is_more_file:
                lines = content.splitlines()
                position = is_more_file.group(0).find(is_more_file.group(1))
                content = '\n'.join([line[position:] for line in lines])
                version_type = search(r"Version( ?: ?|.*?\()(.*?)(\(.*\) ?)?(V\d{3,3}R\S+?)(\)| )", content)
                version_type_str = ""
                if version_type:
                    version_type_str = is_more_file.group(1) + version_type.group(0) + "\n"
                # 重新写入文件
                try:
                    with open(root_path + "kpi_" + (file[:file.rfind('.')] if '.' in file else file) + ".txt", 'w') as more_file:
                        more_file.write(version_type_str + content)
                    # 删除文件
                    remove(root_path + file)
                except Exception as e:
                    return

def read_file_with_encoding(file_path, encoding):
    """
    使用指定编码读取文件内容
    """
    try:
        with open(file_path, 'r', encoding=encoding) as f:
            return f.read()
    except Exception as e:
        return ""


def is_text_file(file_path):
    """
    判断文件是否为文本文件，并返回检测到的编码
    """
    try:
        with open(file_path, 'rb') as f:
            raw_data = f.read(1024)  # 读取前1024字节用于检测
            result = detect(raw_data)
            encoding = result['encoding']
            confidence = result['confidence']
            if encoding and confidence > 0.5:
                return True, encoding
            else:
                return False, None
    except Exception as e:
        print(f"读取文件时出错: {e}")
        return False, None

kpiFileCount = 0
MopFileCount = 0
logFileCount = 0
diagFileCount = 0
cmdFileCount = 0
cfgFileCount = 0
# ipNiceFileCount = 0
unknownFileCount = 0
lsdbFileCount = 0  # 新增LSDB文件计数器



def compress(file_name):
    fileInfo = []
    if path.isfile(file_name):
        if '.' in file_name:
            file_name = get_file_prefix(file_name)[0]

    if not path.isdir(file_name):
        print("Path is invalid:" + file_name)
        raise Exception("Path is invalid, suspect failed to download data from EDM:", file_name)
        return False
    
    # 创建 formated_result 文件夹而不是 ZIP 文件
    output_folder = file_name + "_result"
    if not path.exists(output_folder):
        makedirs(output_folder)
        
    i = 0
    global kpiFileCount
    global MopFileCount
    global logFileCount
    global diagFileCount
    global cmdFileCount
    global cfgFileCount
    global unknownFileCount
    global lsdbFileCount
    neList = {}
    timeList = {}
    thread_list = []
    excelpath_list = []
    for root, dirs, files in walk(file_name):
        for file in files:
            tempFile = get_coding(file)
            tempRoot = get_coding(root)
            if file.endswith('.zip') or file.endswith('.ZIP') \
                    or file.endswith('.nic') or file.endswith('.NIC') or file.endswith('.docx') or file.endswith('.png') \
                    or file.endswith('.json'):
                continue

            fileName = path.join(tempRoot, tempFile)

            if file.endswith('.xlsx'):
                excelpath_list.append(fileName)

            if path.isdir(fileName):
                continue
            i += 1
            myThread = MyThread(get_file_info, args=(fileName,))
            myThread.start()
            thread_list.append(myThread)
    data_list = []
    for thread in thread_list:
        thread.join()
        data_list.append(thread.get_result())
    for dataInfo in data_list:
        if isinstance(dataInfo, str):
            if 'UnknownFiles' not in list(neList.keys()):
                neList['UnknownFiles'] = []
            neList['UnknownFiles'].append(dataInfo)
        elif isinstance(dataInfo, list):
            for data_item in dataInfo:
                if data_item['neName'] not in list(neList.keys()):
                    neList[data_item['neName']] = []
                neList[data_item['neName']].append(data_item)
                fileInfo.append(data_item)
                if data_item['fileType'] == 'CMD':
                    format(data_item['fileName'])
        elif isinstance(dataInfo, dict):
            if dataInfo['neName'] not in list(neList.keys()):
                neList[dataInfo['neName']] = []
            neList[dataInfo['neName']].append(dataInfo)
            fileInfo.append(dataInfo)
            if dataInfo['fileType'] == 'CMD':
                format(dataInfo['fileName'])
            try:
                if dataInfo['neName'] != 'UNKNOWN' and dataInfo['fileType'] != 'CMD' and dataInfo['fileType'] != 'INFO'\
                        and dataInfo['fileType'] != 'DIAG' and dataInfo['fileType'] != 'MOP':
                    if dataInfo['neName'] not in list(timeList.keys()):
                        timeList[dataInfo['neName']] = {}
                    if dataInfo['fileType'] not in list(timeList[dataInfo['neName']].keys()):
                        timeList[dataInfo['neName']][dataInfo['fileType']] = {}
                    if dataInfo['startTime'] and dataInfo['startTime'] not in list(timeList[dataInfo['neName']][dataInfo['fileType']].keys()):
                        timeList[dataInfo['neName']][dataInfo['fileType']][time2timestamp(dataInfo['startTime'])] = time2timestamp(dataInfo['endTime'])
            except Exception as e:
                print("Error:", dataInfo)
                print(e)
    print('%s format complete' % strftime('%Y-%m-%d %H:%M:%S'))
    print('==========================================')
    version_list = {}
    type_list = {}
    t_list = []
    unknown_list = []
    for key, items in list(neList.items()):
        # 跳过 UnknownFiles 不复制
        if key == 'UnknownFiles':
            continue
        
        # 处理已知网元的文件
        for item in items:
            # 记录版本和类型
            if item['neVersion'] and key not in list(version_list.keys()):
                version_list[key] = item['neVersion']
            if item['neType'] and key not in list(type_list.keys()):
                type_list[key] = item['neType']
            
            # 跳过以 lsdb_ 开头的文件（这些是 LSDB 提取的源文件）
            fName = path.split(item['fileName'])[-1]
            if fName.startswith('lsdb_'):
                continue
            # 跳过已被删除的文件（如整文件被提取后删除的情况）
            if not path.exists(item['fileName']):
                continue
            # 跳过空文件（0字节）
            if path.getsize(item['fileName']) == 0:
                continue
            # Rollback 和 script 都放在一个 csv 文件中，只复制一个即可
            if item['fileType'] == 'Rollback':
                continue
            
            # 按网元和文件类型创建子目录
            output_ft = item['fileType']
            ne_dir = path.join(output_folder, key.replace('/', '_'))
            type_dir = path.join(ne_dir, output_ft)
            if not path.exists(type_dir):
                makedirs(type_dir)
            
            # 处理文件名冲突 + 内容去重（先比大小，再比 MD5 哈希）
            base_name, ext = path.splitext(fName)
            dest_filename = fName
            dest_full_path = path.join(type_dir, dest_filename)
            counter = 1
            is_duplicate = False
            
            while path.exists(dest_full_path):
                # 高效去重：先比文件大小，相同再比哈希
                if are_files_duplicate(item['fileName'], dest_full_path):
                    # 内容完全相同，跳过写入和统计
                    print(f"  [DUPLICATE] Skipped: {fName} (content identical with existing file)")
                    is_duplicate = True
                    break
                
                # 内容不同，添加序号继续查找
                dest_filename = f'{base_name}_{counter}{ext}'
                dest_full_path = path.join(type_dir, dest_filename)
                counter += 1
            
            # 只有非重复文件才复制并统计
            if not is_duplicate:
                from shutil import copy2
                copy2(item['fileName'], dest_full_path)
                
                # 统计
                if 'KPI' in item['fileType']:
                    kpiFileCount += 1
                elif item['fileType'] == 'LOG':
                    logFileCount += 1
                elif item['fileType'] == 'DIAG':
                    diagFileCount += 1
                elif item['fileType'] == 'CONFIG':
                    cfgFileCount += 1
                elif item['fileType'] == 'MOP':
                    MopFileCount += 1
                elif item['fileType'] == 'CMD':
                    cmdFileCount += 1
                elif item['fileType'] == 'LSDB':
                    lsdbFileCount += 1
                else:
                    unknownFileCount += 1
        
        t_item = {}
        t_item['neName'] = key
        if key in list(version_list.keys()):
            t_item['neVersion'] = version_list[key]
        else:
            t_item['neVersion'] = ''
        if key in list(type_list.keys()):
            t_item['neType'] = type_list[key]
        else:
            t_item['neType'] = ''
        t_item['items'] = items
        t_list.append(t_item)

    print("============ File summary ================")
    print(" KPI File Count    :%s" % kpiFileCount)
    print(" LOG File Count    :%s" % logFileCount)
    print(" DIAG File Count   :%s" % diagFileCount)
    print(" CMD File Count    :%s" % (cmdFileCount + diagFileCount))
    print(" CONFIG File Count :%s" % cfgFileCount)
    print(" MOP File Count    :%s" % MopFileCount)
    print(" LSDB File Count   :%s" % lsdbFileCount)
    # print(" Unknown File Count:%s" % unknownFileCount)  # UnknownFiles 已跳过
    print("Total Files Count :%s" % (kpiFileCount + logFileCount + diagFileCount + cfgFileCount + MopFileCount + cmdFileCount + lsdbFileCount + unknownFileCount))
    print("============   NE summary ================")
    if 'UnknownFiles' in list(neList.keys()):
        neList.pop('UnknownFiles')
    for key, value in list(neList.items()):
        neVersion = ''
        neType = ''
        if key in list(version_list.keys()):
            neVersion = version_list[key]
        if key in list(type_list.keys()):
            neType = type_list[key]
        print(" %-20s%-15s%s" % (key, neType, neVersion))
    print("============  Time range  ================")
    for key, value in list(timeList.items()):
        time_range = get_time_range(value)
        if len(time_range) > 0:
            print("%s %s:" % (key, version_list.get(key, '')))
            for time_item in time_range:
                print("  " + time_item)

    print("==========================================")
    
    # 补充缺失的 neVersion 和 neType（从同网元的其他文件获取）
    for item in fileInfo:
        if not item.get('neVersion') or not item.get('neType'):
            ne_name = item.get('neName', '')
            for src_item in fileInfo:
                if src_item.get('neName') == ne_name:
                    if not item.get('neVersion') and src_item.get('neVersion'):
                        item['neVersion'] = src_item['neVersion']
                    if not item.get('neType') and src_item.get('neType'):
                        item['neType'] = src_item['neType']
                    if item.get('neVersion') and item.get('neType'):
                        break
    
    return fileInfo
def save_csv(fileName, dataList):
    """保存xlsx文件，按neName和fileType降序排序
       生成 xlsx 文件（包含两个 sheet）：
       - 解析数据详情：原始数据
       - 解析数据汇总：设备及文件类型汇总
    """
    xlsxName = ''
    if not dataList:
        print('dataList is null')
        return False
    if not fileName:
        print('fileName is null')
        return False

    if path.isdir(fileName):
        # 对于文件夹输出，xlsx 文件创建在文件夹内部
        xlsxName = path.join(fileName, 'formated_result_summary.xlsx')

    if path.isfile(fileName):
        if '.' in fileName:
            xlsxName = fileName.replace(fileName.split('.')[-1], '') + 'xlsx'
        else:
            xlsxName = fileName + '.xlsx'
    
    # 获取 zip 文件名
    zip_basename = path.basename(fileName) if fileName.endswith('.zip') else path.basename(fileName)
    
    # 过滤掉 fileType 为空的行
    filtered_data = [item for item in dataList if item.get('fileType')]
    
    # 按 neName 降序，相同则按 fileType 降序排序
    filtered_data.sort(key=lambda x: (x.get('neName', ''), x.get('fileType', '')), reverse=True)
    
    # 构建 zipPath 查找表：dest_name -> zipPath
    from os import path as os_path
    zip_path_lookup = {}
    for item in filtered_data:
        if 'zipPath' in item and item['zipPath']:
            fName = os_path.split(item.get('fileName', ''))[-1]
            dest_name = item.get('neName', '').replace('/', '_') + '/' + item.get('fileType', '') + '/' + fName
            if dest_name not in zip_path_lookup:
                zip_path_lookup[dest_name] = item['zipPath']
    
    # 准备详情数据（删除 startTime 和 endTime）
    detail_data = []
    for item in filtered_data:
        if 'neName' in list(item.keys()) and 'fileType' in list(item.keys()) and 'neVersion' in list(item.keys()) \
                and 'fileName' in list(item.keys()) and 'neType' in list(item.keys()):
            # 格式化 relativePath
            fName = os_path.split(item.get('fileName', ''))[-1]
            output_type = item.get('fileType', '')
            relative_path = item.get('neName', '').replace('/', '_') + '/' + output_type + '/' + fName
            
            detail_data.append({
                'neName': item['neName'],
                'fileType': output_type,
                'neVersion': item['neVersion'],
                'zipFile': zip_basename,
                'relativePath': relative_path,
                'neType': item['neType']
            })
    
    # 如果 openpyxl 可用，生成 xlsx 文件
    if HAS_OPENPYXL:
        wb = Workbook()
        
        # Sheet 1: 解析数据详情
        ws1 = wb.active
        ws1.title = "解析数据详情"
        
        # 写入表头（删除 startTime 和 endTime）
        detail_header = ['neName', 'fileType', 'neVersion', 'zipFile', 'relativePath', 'neType']
        ws1.append(detail_header)
        
        # 写入数据
        for row in detail_data:
            ws1.append([row['neName'], row['fileType'], row['neVersion'], 
                       row['zipFile'], row['relativePath'], row['neType']])
        
        # Sheet 2: 解析数据汇总
        ws2 = wb.create_sheet(title="解析数据汇总")
        
        # 汇总逻辑：统计每个设备的文件类型
        ne_summary = {}
        for item in filtered_data:
            ne_name = item.get('neName', 'UNKNOWN')
            file_type = item.get('fileType', '')
            ne_version = item.get('neVersion', '')
            ne_type = item.get('neType', '')
            
            if ne_name not in ne_summary:
                ne_summary[ne_name] = {
                    'neVersion': ne_version,
                    'neType': ne_type,
                    'fileTypes': set()
                }
            # 保留最新的版本和类型信息
            if ne_version and ne_version != 'UNKNOWN':
                ne_summary[ne_name]['neVersion'] = ne_version
            if ne_type and ne_type != 'UNKNOWN':
                ne_summary[ne_name]['neType'] = ne_type
            if file_type:
                ne_summary[ne_name]['fileTypes'].add(file_type)
        
        # 确定所有文件类型列（只保留常用的文件类型）
        all_file_types = ['CONFIG', 'CMD', 'LOG', 'DIAG', 'KPI', 'LSDB']
        
        # 按 neName 降序排序
        sorted_ne_names = sorted(ne_summary.keys(), reverse=True)
        
        # 写入表头（添加 neType 列）
        summary_header = ['neName', 'neType', 'neVersion'] + all_file_types
        ws2.append(summary_header)
        
        # 写入汇总数据
        summary_data = []
        for ne_name in sorted_ne_names:
            info = ne_summary[ne_name]
            row = [ne_name, info.get('neType', ''), info['neVersion']]
            for ft in all_file_types:
                if ft in info['fileTypes']:
                    row.append('OK')
                else:
                    row.append('NOK')
            ws2.append(row)
            
            # 收集 JSON 数据
            summary_data.append({
                'neName': ne_name,
                'neType': info.get('neType', ''),
                'neVersion': info['neVersion'],
                **{ft: 'OK' if ft in info['fileTypes'] else 'NOK' for ft in all_file_types}
            })
        
        # 调整列宽
        for ws in [ws1, ws2]:
            for column in ws.columns:
                max_length = 0
                column_letter = column[0].column_letter
                for cell in column:
                    try:
                        if len(str(cell.value)) > max_length:
                            max_length = len(str(cell.value))
                    except:
                        pass
                adjusted_width = min(max_length + 2, 50)
                ws.column_dimensions[column_letter].width = adjusted_width
        
        # 保存 xlsx 文件
        wb.save(xlsxName)
        print(f"Summary saved to {xlsxName}")
        
        # 生成 JSON 文件（解析数据汇总）
        import json
        json_name = xlsxName.replace('.xlsx', '.json')
        with open(json_name, 'w', encoding='utf-8') as f:
            json.dump(summary_data, f, ensure_ascii=False, indent=2)
        # JSON 移动和打印由主程序处理
    
    return xlsxName
def get_coding(strData):
    #该方法在py3下已废弃
    if isinstance(strData, str):
        return strData
    coding = sys.getdefaultencoding()
    if not isinstance(strData, str):
        sys.setdefaultencoding(detect(strData)['encoding'])
    try:
        fileName = strData.encode(detect(strData)['encoding']).decode('utf-8')
    except:
        fileName = strData.encode(detect(strData)['encoding']).decode("gbk")
    sys.setdefaultencoding(coding)
    return fileName
#main, fileName = sys.argv[:2]
# Accept input file path from command line argv, default to empty

# 检查并安装依赖
def check_and_install_deps():
    deps = ['chardet', 'pandas']
    import importlib
    for dep in deps:
        try:
            importlib.import_module(dep)
        except ImportError:
            print(f'Installing {dep}...')
            import subprocess
            subprocess.check_call([sys.executable, '-m', 'pip', 'install', dep, '-q'])

check_and_install_deps()

# 收集所有输入文件/目录
input_paths = sys.argv[1:] if len(sys.argv) > 1 else []
if not input_paths:
    input_paths = []

type = 'SR'
if '--rfc' in input_paths:
    input_paths.remove('--rfc')
    type = 'RFC'

clear_cache_flag = False
if '--clear-cache' in input_paths:
    input_paths.remove('--clear-cache')
    clear_cache_flag = True
    print('[CACHE] --clear-cache flag detected, will clear CLI boundary cache')

if clear_cache_flag:
    _CLI_BOUNDARY_CACHE.clear()
    print('[CACHE] CLI boundary cache cleared')

print(type)
print('APP release time: 2021-01-22')

if len(input_paths) == 0:
    print('Usage: python ifilter.py <file1.zip> [file2.zip] [...] [--rfc] [--clear-cache]')
    print('       python ifilter.py <dir1> [dir2] [...] [--rfc] [--clear-cache]')
    exit(1)

print('%s Start' % strftime('%Y-%m-%d %H:%M:%S'))

import shutil

for input_path in input_paths:
    # 处理每个输入路径：将所有文件合并后统一输出
    output_timestamp = strftime('%Y%m%d_%H%M%S')
    
    # 确定根目录
    root_dir = path.dirname(input_path) if path.isfile(input_path) else input_path
    if not path.dirname(input_path) and not path.isdir(input_path):
        root_dir = '.'
    
    # 清理已有的输出结果（递归删除根目录下所有 formated_result 和 _result 相关文件夹和文件）
    # 包括：formated_result/、_result/、formated_result.json、formated_result_summary.xlsx 等
    old_result_dir = path.join(root_dir, 'formated_result')
    old_result_dir_alt = path.join(root_dir, root_dir.split('\\')[-1] + '_result') if path.isdir(root_dir) else ''
    old_json_file = path.join(root_dir, 'formated_result.json')
    old_xlsx_file = path.join(root_dir, 'formated_result_summary.xlsx')
    
    # 清理根目录的输出物
    if path.exists(old_result_dir):
        print(f'Removing existing formated_result directory: {old_result_dir}')
        shutil.rmtree(old_result_dir, ignore_errors=True)
    if old_result_dir_alt and path.exists(old_result_dir_alt) and old_result_dir_alt != old_result_dir:
        print(f'Removing existing _result directory: {old_result_dir_alt}')
        shutil.rmtree(old_result_dir_alt, ignore_errors=True)
    if path.exists(old_json_file):
        print(f'Removing existing formated_result.json...')
        remove(old_json_file)
    if path.exists(old_xlsx_file):
        print(f'Removing existing formated_result_summary.xlsx...')
        remove(old_xlsx_file)
    
    # 递归删除所有子目录下的 formated_result、_merged_temp_* 和 _result 文件夹以及所有相关输出文件
    result_patterns_files = {'formated_result.json', 'formated_result_summary.xlsx', 'formated_result.txt', 'formated_result.log'}
    for r, dirs, files in walk(root_dir):
        # 删除匹配的输出文件
        for f in files:
            if f in result_patterns_files:
                try:
                    remove(path.join(r, f))
                    print(f'Removing nested output file: {path.join(r, f)}')
                except:
                    pass
        # 删除匹配的输出目录（formated_result/、*_result/、_merged_temp_*/）
        for d in dirs[:]:
            if d == 'formated_result' or d.endswith('_result') or d.startswith('_merged_temp_'):
                target_dir = path.join(r, d)
                try:
                    shutil.rmtree(target_dir, ignore_errors=True)
                    print(f'Removing nested output dir: {target_dir}')
                except:
                    pass
    
    # 收集所有文件（递归遍历，跳过已有的输出目录和临时目录）
    archive_files = []
    direct_files = []
    
    for r, dirs, files in walk(root_dir):
        # 跳过输出目录和临时目录
        dirs[:] = [d for d in dirs if d != 'formated_result' and not d.startswith('_merged_temp_')]
        
        for f in files:
            fp = path.join(r, f)
            if f.lower().endswith('.zip'):
                archive_files.append(fp)
            else:
                direct_files.append(fp)
    
    if len(archive_files) + len(direct_files) == 0:
        print(f'No valid files found in {root_dir}.')
        continue
    
    print(f'Found {len(archive_files) + len(direct_files)} file(s) in {path.basename(root_dir)}')
    if archive_files:
        print(f'  - {len(archive_files)} archive(s) to extract')
    if direct_files:
        print(f'  - {len(direct_files)} direct file(s)')
    
    # 创建合并输出的临时目录
    merged_temp_dir = path.join(root_dir, f'_merged_temp_{output_timestamp}')
    if not path.exists(merged_temp_dir):
        makedirs(merged_temp_dir)
    
    # 处理压缩包（解压）
    all_dataList = []
    for i, f in enumerate(archive_files):
        print(f'Extracting archive [{i+1}/{len(archive_files)}]: {path.basename(f)}')
        
        try:
            with ZipFile(f) as z:
                z.extractall(merged_temp_dir)
        except Exception as e:
            print(f'  Error extracting zip file: {e}')
    
    # 递归解压所有嵌套的 zip 文件
    def extract_all_nested_zips(directory):
        processed_files = set()
        max_iterations = 50
        iteration = 0
        valid_patterns = ['diag', 'log', 'diaglog', 'logfile', 'kpi']
        
        while iteration < max_iterations:
            iteration += 1
            found_new = False
            
            for r, dirs, files in walk(directory):
                path_lower = r.lower()
                is_valid_path = any(p in path_lower for p in valid_patterns)
                
                for file in files:
                    file_path = path.join(r, file)
                    file_lower = file.lower()
                    
                    if not file_lower.endswith('.zip'):
                        continue
                        
                    if not is_valid_path and not any(p in file_lower for p in valid_patterns):
                        continue
                    
                    if file_path in processed_files:
                        continue
                        
                    processed_files.add(file_path)
                    
                    try:
                        if path.getsize(file_path) == 0:
                            continue
                            
                        print(f'  Extracting nested archive: {path.basename(file_path)}')
                        found_new = True
                        
                        with ZipFile(file_path) as z:
                            z.extractall(r)
                        
                        try:
                            remove(file_path)
                        except:
                            pass
                    except Exception as e:
                        print(f'  Error extracting {file_path}: {e}')
            
            if not found_new:
                break
        
        print(f'  Nested archive extraction completed ({len(processed_files)} archives processed)')
    
    extract_all_nested_zips(merged_temp_dir)
    
    # 处理普通文件（直接复制到临时目录）
    if direct_files:
        print(f'Copying {len(direct_files)} direct files...')
        from shutil import copy2
        for i, f in enumerate(direct_files):
            try:
                dest = path.join(merged_temp_dir, path.basename(f))
                counter = 1
                while path.exists(dest):
                    name, ext = path.splitext(path.basename(f))
                    dest = path.join(merged_temp_dir, f'{name}_{counter}{ext}')
                    counter += 1
                copy2(f, dest)
            except Exception as e:
                print(f'  Error copying {f}: {e}')
    
    # KPI 检查
    print('Pre-checking KPI files...')
    pre_check_kpi_dir(walk(merged_temp_dir))
    
    # 压缩处理
    print('Compressing data...')
    print('%s Compressing files' % strftime('%Y-%m-%d %H:%M:%S'))
    all_dataList = compress(merged_temp_dir)
    
    print('==========================================')
    
    # 输出结果
    if all_dataList:
        result_temp_dir = merged_temp_dir + "_result"
        if path.exists(result_temp_dir):
            final_result_dir = path.join(root_dir, 'formated_result')
            
            if path.exists(final_result_dir):
                shutil.rmtree(final_result_dir, ignore_errors=True)
            
            shutil.move(result_temp_dir, final_result_dir)
            print(f'\nMerged output saved to: {final_result_dir}')
            
            # 保存汇总 xlsx
            print('%s Saving to xlsx file' % strftime('%Y-%m-%d %H:%M:%S'))
            xlsx_file = save_csv(final_result_dir, all_dataList)
            
            # JSON 文件
            json_file = xlsx_file.replace('.xlsx', '.json')
            json_final_path = path.join(root_dir, 'formated_result.json')
            if path.exists(json_file):
                if path.exists(json_final_path):
                    remove(json_final_path)
                shutil.move(json_file, json_final_path)
                print(f'JSON saved to: {json_final_path}')
            else:
                print(f'Warning: JSON file not found at {json_file}')
        else:
            print('Warning: Expected output folder not found')
    else:
        print('No data to save.')
    
    # 清理临时目录前，确认所有工作线程已结束，避免后台线程仍在写入该目录导致文件丢失
    for t in threading.enumerate():
        if isinstance(t, MyThread) and t.is_alive():
            t.join()
    # 清理临时目录
    if path.exists(merged_temp_dir):
        shutil.rmtree(merged_temp_dir, ignore_errors=True)

print('%s Finished' % strftime('%Y-%m-%d %H:%M:%S'))
