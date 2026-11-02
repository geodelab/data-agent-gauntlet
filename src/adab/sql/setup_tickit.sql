-- Loads the Amazon Redshift TICKIT sample dataset into schema `bench`.
-- Source files: the public sample bucket used by the Amazon Redshift Getting Started guide.
-- Requires the namespace default IAM role to be able to read s3://redshift-downloads/.

DROP SCHEMA IF EXISTS bench CASCADE;
CREATE SCHEMA bench;

CREATE TABLE bench.users (
  userid INTEGER NOT NULL DISTKEY SORTKEY,
  username CHAR(8),
  firstname VARCHAR(30),
  lastname VARCHAR(30),
  city VARCHAR(30),
  state CHAR(2),
  email VARCHAR(100),
  phone CHAR(14),
  likesports BOOLEAN,
  liketheatre BOOLEAN,
  likeconcerts BOOLEAN,
  likejazz BOOLEAN,
  likeclassical BOOLEAN,
  likeopera BOOLEAN,
  likerock BOOLEAN,
  likevegas BOOLEAN,
  likebroadway BOOLEAN,
  likemusicals BOOLEAN
);

CREATE TABLE bench.venue (
  venueid SMALLINT NOT NULL DISTKEY SORTKEY,
  venuename VARCHAR(100),
  venuecity VARCHAR(30),
  venuestate CHAR(2),
  venueseats INTEGER
);

CREATE TABLE bench.category (
  catid SMALLINT NOT NULL DISTKEY SORTKEY,
  catgroup VARCHAR(10),
  catname VARCHAR(10),
  catdesc VARCHAR(50)
);

CREATE TABLE bench.date (
  dateid SMALLINT NOT NULL DISTKEY SORTKEY,
  caldate DATE NOT NULL,
  day CHARACTER(3) NOT NULL,
  week SMALLINT NOT NULL,
  month CHARACTER(5) NOT NULL,
  qtr CHARACTER(5) NOT NULL,
  year SMALLINT NOT NULL,
  holiday BOOLEAN DEFAULT ('N')
);

CREATE TABLE bench.event (
  eventid INTEGER NOT NULL DISTKEY,
  venueid SMALLINT NOT NULL,
  catid SMALLINT NOT NULL,
  dateid SMALLINT NOT NULL SORTKEY,
  eventname VARCHAR(200),
  starttime TIMESTAMP
);

CREATE TABLE bench.listing (
  listid INTEGER NOT NULL DISTKEY,
  sellerid INTEGER NOT NULL,
  eventid INTEGER NOT NULL,
  dateid SMALLINT NOT NULL SORTKEY,
  numtickets SMALLINT NOT NULL,
  priceperticket DECIMAL(8,2),
  totalprice DECIMAL(8,2),
  listtime TIMESTAMP
);

CREATE TABLE bench.sales (
  salesid INTEGER NOT NULL,
  listid INTEGER NOT NULL DISTKEY,
  sellerid INTEGER NOT NULL,
  buyerid INTEGER NOT NULL,
  eventid INTEGER NOT NULL,
  dateid SMALLINT NOT NULL SORTKEY,
  qtysold SMALLINT NOT NULL,
  pricepaid DECIMAL(8,2),
  commission DECIMAL(8,2),
  saletime TIMESTAMP
);

COPY bench.users FROM 's3://redshift-downloads/tickit/allusers_pipe.txt' IAM_ROLE DEFAULT DELIMITER '|' REGION 'us-east-1';
COPY bench.venue FROM 's3://redshift-downloads/tickit/venue_pipe.txt' IAM_ROLE DEFAULT DELIMITER '|' REGION 'us-east-1';
COPY bench.category FROM 's3://redshift-downloads/tickit/category_pipe.txt' IAM_ROLE DEFAULT DELIMITER '|' REGION 'us-east-1';
COPY bench.date FROM 's3://redshift-downloads/tickit/date2008_pipe.txt' IAM_ROLE DEFAULT DELIMITER '|' REGION 'us-east-1';
COPY bench.event FROM 's3://redshift-downloads/tickit/allevents_pipe.txt' IAM_ROLE DEFAULT DELIMITER '|' TIMEFORMAT 'YYYY-MM-DD HH:MI:SS' REGION 'us-east-1';
COPY bench.listing FROM 's3://redshift-downloads/tickit/listings_pipe.txt' IAM_ROLE DEFAULT DELIMITER '|' REGION 'us-east-1';
COPY bench.sales FROM 's3://redshift-downloads/tickit/sales_tab.txt' IAM_ROLE DEFAULT DELIMITER '\t' TIMEFORMAT 'MM/DD/YYYY HH:MI:SS' REGION 'us-east-1';

GRANT USAGE ON SCHEMA bench TO PUBLIC;
GRANT SELECT ON ALL TABLES IN SCHEMA bench TO PUBLIC;
