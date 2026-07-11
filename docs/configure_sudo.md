# configure `sudo`

after setting sudo you must log out and back in to apply group changes

```bash
su --login
apt update && apt install sudo
adduser <your-username> sudo
exit
```