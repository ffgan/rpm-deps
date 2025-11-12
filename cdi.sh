#! /bin/bash


# Freezing nginx to avoid segfaults when pulling images with tls
# May get fixed with 1.25
NGINX_VERSION="1:1.22.1-4.module_el9+666+132dc76f"

# Packages that we want to be included in all container images.
#
# Further down we define per-image package lists, which are just like
# this one are split into two: one for the packages that we actually
# want to have in the image, and one for (indirect) dependencies that
# have more than one way of being resolved. Listing the latter
# explicitly ensures that bazeldnf always reaches the same solution
# and thus keeps things reproducible
centos_base="
  ca-certificates
  crypto-policies
  acl
  curl
  vim-minimal
  util-linux-core
"
centos_extra="
  coreutils-single
  glibc-minimal-langpack
  libcurl-minimal
  tar
"

cdi_importer="
libnbd
libstdc++
nbdkit-server
nbdkit-basic-filters
nbdkit-curl-plugin
nbdkit-xz-filter
nbdkit-gzip-filter
qemu-img
python3-pycurl
python3-six
"

cdi_importer_extra_x86_64="
nbdkit-vddk-plugin
sqlite-libs
ovirt-imageio-client
python3-ovirt-engine-sdk4
"

cdi_importer_extra_riscv64="
nbdkit-vddk-plugin
sqlite-libs
ovirt-imageio-client
python3-ovirt-engine-sdk4
"

cdi_uploadserver="
libnbd
qemu-img
"

testimage="
crypto-policies-scripts
qemu-img
nginx-${NGINX_VERSION}
python3-systemd
systemd-libs
openssl
buildah
"

python rpm.py --packages PACKAGES $centos_base $centos_extra $testimage --rpmtree testimage_riscv64

python rpm.py --packages PACKAGES $centos_base $centos_extra --rpmtree centos_base_riscv64

python rpm.py --packages PACKAGES $centos_base $centos_extra $cdi_importer $cdi_importer_extra_riscv64 --rpmtree cdi_importer_base_riscv64

python rpm.py --packages PACKAGES $centos_base $centos_extra $cdi_uploadserver --rpmtree cdi_uploadserver_base_riscv64
