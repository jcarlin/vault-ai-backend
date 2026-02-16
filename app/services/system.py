import psutil

from app.schemas.system import SystemResources


async def get_system_resources() -> SystemResources:
    """Collect CPU, RAM, disk, network, and temperature metrics via psutil."""
    cpu_pct = psutil.cpu_percent(interval=0.1)
    mem = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
    net = psutil.net_io_counters()

    cpu_temp = None
    if hasattr(psutil, "sensors_temperatures"):
        try:
            temps = psutil.sensors_temperatures()
            for _name, entries in temps.items():
                if entries:
                    cpu_temp = entries[0].current
                    break
        except Exception:
            pass

    return SystemResources(
        cpu_usage_pct=cpu_pct,
        cpu_count=psutil.cpu_count(),
        ram_total_mb=mem.total // (1024 * 1024),
        ram_used_mb=mem.used // (1024 * 1024),
        ram_usage_pct=mem.percent,
        disk_total_gb=round(disk.total / (1024**3), 1),
        disk_used_gb=round(disk.used / (1024**3), 1),
        disk_usage_pct=disk.percent,
        network_in_bytes=net.bytes_recv,
        network_out_bytes=net.bytes_sent,
        temperature_celsius=cpu_temp,
    )
