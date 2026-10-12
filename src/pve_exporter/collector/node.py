"""
Prometheus collectors for Proxmox VE cluster.
"""

# pylint: disable=too-few-public-methods

import itertools
import re
from datetime import datetime

from prometheus_client.core import GaugeMetricFamily


class NodeConfigCollector:
    """
    Collects Proxmox VE VM information directly from config, i.e. boot, name, onboot, etc.
    For manual test: "pvesh get /nodes/<node>/<type>/<vmid>/config"

    # HELP pve_onboot_status Proxmox vm config onboot value
    # TYPE pve_onboot_status gauge
    pve_onboot_status{id="qemu/113",node="XXXX",type="qemu"} 1.0
    """

    def __init__(self, pve):
        self._pve = pve

    def collect(self):  # pylint: disable=missing-docstring
        metrics = {
            'onboot': GaugeMetricFamily(
                'pve_onboot_status',
                'Proxmox vm config onboot value',
                labels=['id', 'name', 'node', 'type'],
            ),
            'disk': GaugeMetricFamily(
                'pve_disk_info',
                'Proxmox disk that belongs to a guest',
                labels=['id', 'name', 'node', 'type', 'size', 'diskname'],
            ),
        }

        node = None
        for entry in self._pve.cluster.status.get():
            if entry['type'] == 'node' and entry['local']:
                node = entry['name']
                break

        # Scrape qemu config
        vmtype = 'qemu'
        for vmdata in self._pve.nodes(node).qemu.get():
            config = self._pve.nodes(node).qemu(vmdata['vmid']).config.get().items()
            for key, metric_value in config:
                default_label_values = [
                    f"{vmtype}/{vmdata['vmid']}",
                    vmdata['name'],
                    node,
                    vmtype,
                ]
                metric_name = self.adapt_metric_name(key)
                if metric_name == 'disk':
                    # metric_value contains the value string for the given disk key
                    diskinfo = self.value_to_dict(metric_value)
                    if diskinfo.get('media') and diskinfo.get('media') == 'cdrom':
                        continue
                    default_label_values = default_label_values + [
                        diskinfo['size'],
                        diskinfo['name'],
                    ]
                    metric_value = self.convert_size_to_bytes(diskinfo["size"])
                if metric_name in metrics:
                    metrics[metric_name].add_metric(default_label_values, metric_value)

        # Scrape LXC config
        vmtype = 'lxc'
        for vmdata in self._pve.nodes(node).lxc.get():
            config = self._pve.nodes(node).lxc(vmdata['vmid']).config.get().items()
            for metric_name, metric_value in config:
                default_label_values = [f"{vmtype}/{vmdata['vmid']}", node, vmtype]
                if metric_name in metrics:
                    metrics[metric_name].add_metric(default_label_values, metric_value)

        return metrics.values()

    def adapt_metric_name(self, raw_metric_name: str) -> str:
        """
        Proxmox returns <disktype>0-3 e.g sata2.
        The regex below checks if the given string is a disk 
        and replaces the string with <disk>
        """
        return re.sub(r'(efidisk|sata|ide|virtio)([0-3])', 'disk', raw_metric_name)

    def value_to_dict(self, proxmox_value_string) -> dict[str, str]:
        """
        Proxmox returns values of items in the json as strings,
        specifically the disks have an unsorted string.
        E.g: 'zfs-local:<diskname>,media=...,size=...,[...]'
        """
        split_str = str(proxmox_value_string).split(',')
        return_dict = {}
        for item in split_str:
            if ':' in item:
                disktype, name = item.split(':')
                return_dict['type'] = disktype.strip()
                return_dict['name'] = name.strip()
            if '=' in item:
                key, value = item.split('=')
                return_dict[key.strip()] = value.strip()
        return return_dict

    def convert_size_to_bytes(self,size_str) -> int:
        """Convert proxmox sizes into bytes.
        """
        multipliers = {
            'K': 1024,
            'M': 1024**2,
            'G': 1024**3,
            'T': 1024**4,
            'P': 1024**5,
            'E': 1024**6,
            'Z': 1024**7,
            'Y': 1024**8,
        }

        for suffix, value in multipliers.items():
            if size_str.endswith(suffix):
                return int(float(size_str[0:-len(suffix)]) * value)
        # This should never be hit.
        return 0

class NodeReplicationCollector:
    """
    Collects Proxmox VE Replication information directly from status, i.e. replication duration,
    last_sync, last_try, next_sync, fail_count.
    For manual test: "pvesh get /nodes/<node>/replication/<id>/status"
    """

    def __init__(self, pve):
        self._pve = pve

    def collect(self):  # pylint: disable=missing-docstring

        info_metrics = {
            'info': GaugeMetricFamily(
                'pve_replication_info',
                'Proxmox vm replication info',
                labels=['id', 'type', 'source', 'target', 'guest'],
            )
        }

        metrics = {
            'duration': GaugeMetricFamily(
                'pve_replication_duration_seconds',
                'Proxmox vm replication duration',
                labels=['id'],
            ),
            'last_sync': GaugeMetricFamily(
                'pve_replication_last_sync_timestamp_seconds',
                'Proxmox vm replication last_sync',
                labels=['id'],
            ),
            'last_try': GaugeMetricFamily(
                'pve_replication_last_try_timestamp_seconds',
                'Proxmox vm replication last_try',
                labels=['id'],
            ),
            'next_sync': GaugeMetricFamily(
                'pve_replication_next_sync_timestamp_seconds',
                'Proxmox vm replication next_sync',
                labels=['id'],
            ),
            'fail_count': GaugeMetricFamily(
                'pve_replication_failed_syncs',
                'Proxmox vm replication fail_count',
                labels=['id'],
            ),
        }

        node = None
        for entry in self._pve.cluster.status.get():
            if entry['type'] == 'node' and entry['local']:
                node = entry['name']
                break

        for jobdata in self._pve.nodes(node).replication.get():
            # Add info metric
            label_values = [
                str(jobdata['id']),
                str(jobdata['type']),
                f"node/{jobdata['source']}",
                f"node/{jobdata['target']}",
                f"{jobdata['vmtype']}/{jobdata['guest']}",
            ]
            info_metrics['info'].add_metric(label_values, 1)

            # Add metrics
            label_values = [str(jobdata['id'])]
            status = self._pve.nodes(node).replication(jobdata['id']).status.get()
            for key, metric_value in status.items():
                if key in metrics:
                    metrics[key].add_metric(label_values, metric_value)

        return itertools.chain(metrics.values(), info_metrics.values())


class SubscriptionCollector:
    """
    Collects Proxmox VE subscription information (node, subscription level, status, next due date).
    """

    def __init__(self, pve):
        self._pve = pve

    def collect(self):  # pylint: disable=missing-docstring
        info_metric = GaugeMetricFamily(
            "pve_subscription_info",
            "Proxmox VE subscription info (1 if present)",
            labels=['id', 'level'],
        )

        possible_statuses = [
            'new',
            'notfound',
            'active',
            'invalid',
            'expired',
            'suspended',
        ]
        status_metric = GaugeMetricFamily(
            "pve_subscription_status",
            "Proxmox VE subscription status (1 if matches status)",
            labels=['id', 'status'],
        )

        next_due_metric = GaugeMetricFamily(
            "pve_subscription_next_due_timestamp_seconds",
            "Subscription next due date as Unix timestamp",
            labels=['id'],
        )

        node = None
        for entry in self._pve.cluster.status.get():
            if entry['type'] == 'node' and entry['local']:
                node = entry['name']
                break

        subscription = self._pve.nodes(node).subscription.get()

        level = subscription.get('level', 'unknown')
        status = subscription.get('status', 'unknown')

        info_metric.add_metric(
            [f'node/{node}', level],
            1,
        )

        for possible_status in possible_statuses:
            value = 1 if status == possible_status else 0
            status_metric.add_metric(
                [f'node/{node}', possible_status],
                value,
            )

        next_due_date = subscription.get('nextduedate')
        if next_due_date:
            timestamp = datetime.strptime(next_due_date, '%Y-%m-%d').timestamp()
            next_due_metric.add_metric(
                [f'node/{node}'],
                timestamp,
            )

        yield info_metric
        yield status_metric
        yield next_due_metric
