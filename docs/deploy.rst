======================
Installing / Deploying
======================

MySQL / RDS
===========

The application is written and tested against MySQL 5.6.x or Amazon RDS of the
same version. The default configuration works for the most part. There are
just three changes you need to do. For example via the my.cnf:

.. code-block:: ini

    [mysqld]
    innodb_file_format=Barracuda
    innodb_strict_mode=on
    sql-mode="STRICT_TRANS_TABLES"


Code
====

Run the following commands to download the database and the server:

.. code-block:: bash

   git clone https://github.com/mozilla/ichnaea
   cd ichnaea
   make

And run the server:

.. code-block:: bash

   bin/circusd circus.ini

This command will launch 2 web server processes, one celery beat daemon and
two celery worker processes. You can access the web service on port 7001.

You can also run the service as a daemon:

.. code-block:: bash

   bin/circusd --daemon circus.ini

And interact with it using circusctl. Have a look at `the Circus documentation
<https://circus.readthedocs.org/>`_ for more information on this.


Logging
=======

Logging events are processed by hekad.  A basic hekad.toml
configuration is included for local development, that will route
messages to carbon and display in graphite.

Installing Graphite
===================

You will need both graphite and carbon to collect timing and count
messages.

OSX + Homebrew

    mkvirtualenv graphite

    brew install cairo
    brew install py2cairo

    pip install Django==1.5
    pip install django-tagging
    pip install carbon
    pip install whisper
    pip install graphite-web
    pip install Twisted==11.1.0 

Edit /opt/graphite/webapp/graphite/local_settings.py and edit the
DATA_DIRS to be ::

    DATA_DIRS = ["/opt/graphite/storage/whisper"]

Edit /opt/graphite/conf/carbon.conf and set the LOCAL_DATA_DIR setting ::

    LOCAL_DATA_DIR = /opt/graphite/storage/whisper/

Now you should be able to use ichnaea and get pretty graphs.
To start carbon and listen for statsd messagse on 127.0.0.1:2004 ::

    workon graphite
    export PYTHONPATH=/usr/local/lib/python2.7/site-packages:$PYTHONPATH
    python /opt/graphite/bin/carbon-cache.py start

Startup graphite-web by using this ::

    workon graphite
    export PYTHONPATH=/usr/local/lib/python2.7/site-packages:$PYTHONPATH
    python /opt/graphite/bin/run-graphite-devel-server.py /opt/graphite

Startup hekad 0.4.2 with ::

    hekad --config=/your/ichnaea/path/hekad.toml

Your ichnaea metrics should now show up when you point your browser to
http://localhost:8080/

You can optionally disable the logging to stdout with hekad by
commenting out the [LogOutput] section of the hekad.toml file.
