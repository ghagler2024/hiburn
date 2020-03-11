import logging
import ipaddress
import os
from . import utils


# -------------------------------------------------------------------------------------------------
class Action:
    @classmethod
    def _run(cls, client, config, args):
        return cls(client, config).run(args)

    def __init__(self, client, config):
        self.client = client
        self.config = config

    @classmethod
    def add_arguments(cls, parser):
        pass

    def run(self, args):
        raise NotImplementedError()

    # some helper methods are below
    @property
    def host_ip_interface(self):
        return ipaddress.ip_interface(self.config["net"]["host"])

    @property
    def device_ip(self):
        return ipaddress.ip_address(self.config["net"]["target"])

    def configure_network(self):
        self.client.setenv(
            ipaddr=self.device_ip,
            netmask=self.host_ip_interface.netmask,
            serverip=self.host_ip_interface.ip
        )
    
    def upload_files(self, *args):
        utils.upload_files_via_tftp(self.client, args, listen_ip=str(self.host_ip_interface.ip))


def add_actions(parser, *actions):
    subparsers = parser.add_subparsers(title="Action")
    for action in actions:
        action_parser = subparsers.add_parser(action.__name__,
            help=action.__doc__.strip() if action.__doc__ else None
        )
        action.add_arguments(action_parser)
        action_parser.set_defaults(action=action._run)


# -------------------------------------------------------------------------------------------------
class printenv(Action):
    """ Print U-Boot environment variables
    """
    def run(self, args):
        result = self.client.printenv()
        print("\n".join(result))


# -------------------------------------------------------------------------------------------------
class ping(Action):
    """ Configure network on device and ping host
    """
    def run(self, args):
        self.configure_network()
        result = self.client.ping(self.host_ip_interface.ip)[-1]
        if not result.endswith("is alive"):
            raise RuntimeError("network is unavailable")


# -------------------------------------------------------------------------------------------------
class download(Action):
    """ Download data from device's memory via TFTP
    """
    @classmethod
    def add_arguments(cls, parser):
        parser.add_argument("--dst", type=str, default="./dump", help="Destination file")
        parser.add_argument("--addr", type=utils.hsize2int, required=True, help="Address to start downloading from")
        parser.add_argument("--size", type=utils.hsize2int, required=True, help="Amount of bytes to be downloaded")

    def run(self, args):
        self.configure_network()
        utils.download_files_via_tftp(self.client, (
            (args.dst, args.addr, args.size),
        ), listen_ip=str(self.host_ip_interface.ip))


# -------------------------------------------------------------------------------------------------
class upload(Action):
    """ Upload data to device's memory via TFTP
    """
    @classmethod
    def add_arguments(cls, parser):
        parser.add_argument("--src", type=str, required=True, help="File to be uploaded")
        parser.add_argument("--addr", type=utils.hsize2int, required=True, help="Destination address in device's memory")

    def run(self, args):
        self.configure_network()
        self.upload_files((args.src, args.addr))


# -------------------------------------------------------------------------------------------------
class boot(Action):
    """ Upload Kernel and RootFS images to device and boot using them
    """
    @classmethod
    def add_arguments(cls, parser):
        parser.add_argument("--uimage", type=str, required=True, help="Kernel UImage file")
        parser.add_argument("--rootfs", type=str, required=True, help="RootFS image file")
    
    def run(self, args):
        BLOCK_SIZE = self.config["mem"]["block_size"]
        BASE_ADDR = self.config["mem"]["base_addr"]

        self.configure_network()

        uimage_addr = BASE_ADDR
        rootfs_addr = utils.aligned_address(BLOCK_SIZE, uimage_addr + os.path.getsize(args.uimage))
        self.upload_files((args.uimage, uimage_addr), (args.rootfs, rootfs_addr))

        bootargs = ""
        bootargs += "mem={} ".format(self.config["mem"]["linux_size"])
        bootargs += "console={} ".format(self.config["linux_console"])
        bootargs += "ip={}:{}:{}:{}:camera1::off; ".format(
            self.device_ip, self.host_ip_interface.ip, self.host_ip_interface.ip, self.host_ip_interface.netmask
        )
        bootargs += "mtdparts=hi_sfc:512k(boot) "
        bootargs += "root=/dev/ram0 ro initrd={:#x},{}".format(rootfs_addr, self.config["mem"]["initrd_size"])

        logging.info("Load kernel with bootargs: {}".format(bootargs))

        self.client.setenv(bootargs=bootargs)
        self.client.bootm(uimage_addr)
        logging.info("OS seems successfully started")
