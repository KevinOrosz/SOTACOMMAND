"""Copy the studio vault to the backup drive."""
import shutil

SOURCE = r"C:\Users\AveryE\Documents\Vault"
MIRROR = "C:\\Users\\AveryE\\Backups"
LINUX_MIRROR = "/home/averye/vault"

def main():
    shutil.copytree(SOURCE, MIRROR)
