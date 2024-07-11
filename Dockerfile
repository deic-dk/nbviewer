# nbviewer for use on the ScienceData infrastructure.
# Build with
# docker build -t sciencedata/nbviewer_sciencedata .
# push with
# docker push sciencedata/nbviewer_sciencedata
#

# Define a builder image
FROM python:3.7-buster as builder

ENV DEBIAN_FRONTEND=noninteractive
ENV LANG=C.UTF-8
RUN apt-get update \
 && apt-get install -yq --no-install-recommends \
    ca-certificates \
    libcurl4-gnutls-dev \
    git \
    nodejs \
    npm

# Python requirements
COPY ./requirements-dev.txt /srv/nbviewer/
COPY ./requirements.txt /srv/nbviewer/
RUN python3 -mpip install -r /srv/nbviewer/requirements-dev.txt -r /srv/nbviewer/requirements.txt

WORKDIR /srv/nbviewer

# Copy source tree in
COPY . /srv/nbviewer
RUN python3 setup.py build && \
    python3 -mpip wheel -vv . -w /wheels

# Now define the runtime image
FROM python:3.7-buster
LABEL maintainer="ScienceData Project <support@sciencedata.dk>"
LABEL vendor="sciencedata.dk"
LABEL version="1.0"
LABEL description="Debian/Python for deployment/integration of Jupyter nbviewer on sciencedata.dk"

ARG NB_USER="sciencedata"
ARG NB_UID="1000"
ARG NB_GID="100"

ENV DEBIAN_FRONTEND=noninteractive
ENV LANG=C.UTF-8

RUN apt-get update \
 && apt-get install -yq --no-install-recommends \
    ca-certificates \
    libcurl4 \
    apt-utils rsyslog openssh-client net-tools inetutils-tools curl \
    psmisc vim pciutils dkms vlan unicode-data gnupg cron \
    apt-transport-https wget jq nfs-common iputils-ping traceroute sudo \
    # Slim down image
    && apt-get clean autoclean \
    && apt-get autoremove -y \
    && rm -rf /var/lib/{apt,dpkg}/ \
    && rm -rf /usr/share/man/* /usr/share/groff/* /usr/share/info/* \
    && find /usr/share/doc -depth -type f ! -name copyright -delete \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /wheels /wheels
RUN python3 -mpip install --no-cache /wheels/*

RUN echo "auth requisite pam_deny.so" >> /etc/pam.d/su && \
    sed -i.bak -e 's/^%admin/#%admin/' /etc/sudoers && \
    sed -i.bak -e 's/^%sudo/#%sudo/' /etc/sudoers && \
    useradd -m -s /bin/bash -N -u $NB_UID $NB_USER && \
    chmod g+w /etc/passwd

RUN echo "$NB_USER:secret" | chpasswd
RUN echo "$NB_USER ALL=(ALL) NOPASSWD: ALL" > /etc/sudoers.d/$NB_USER && chmod 0440 /etc/sudoers.d/$NB_USER

# To change the number of threads use
# docker run -d -e NBVIEWER_THREADS=4 -p 80:8080 nbviewer
ENV NBVIEWER_THREADS 2
WORKDIR /srv/nbviewer
EXPOSE 8080
#USER nobody
USER $NB_USER

EXPOSE 9000
CMD ["python", "-m", "nbviewer", "--port=8080", "--localfiles=/home/sciencedata/", "--rate-limit=600", "--no-cache", "--cache-expiry-min=1", "--cache-expiry-max=2"]
