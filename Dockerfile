FROM centos:7
RUN yum install -y epel-release && yum makecache
RUN yum install -y \
    python3 \
    python3-pip
RUN pip3 install -U  aiozmq==0.7.1 pyzmq
RUN adduser runner

USER runner
