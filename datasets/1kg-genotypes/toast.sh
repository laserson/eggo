#! /usr/bin/env bash
# Licensed to Big Data Genomics (BDG) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The BDG licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.


set -e


# DOWNLOAD VCF FILES
eggo-data dnload_raw \
    --input ~/eggo/datasets/1kg-genotypes/datapackage.json \
    --output hdfs:///user/ec2-user/1kg-genotypes/raw


# QUINCE INGESTION
hadoop jar ~/quince/target/quince-0.0.1-SNAPSHOT-job.jar \
    com.cloudera.science.quince.LoadVariantsTool \
    -D mapreduce.map.java.opts="-Xmx3g" \
    -D mapreduce.reduce.java.opts="-Xmx3g" \
    -D mapreduce.map.memory.mb=4096 \
    -D mapreduce.reduce.memory.mb=4096 \
    --sample-group 1kg \
    --data-model GA4GH \
    --flatten \
    hdfs:///user/ec2-user/1kg-genotypes/raw \
    hdfs:///user/ec2-user/1kg-genotypes/ga4gh_flat
hadoop distcp \
    hdfs:///user/ec2-user/1kg-genotypes/ga4gh_flat \
    s3n://bdg-eggo/1kg-genotypes/ga4gh_flat
hadoop fs -rm -r hdfs:///user/ec2-user/1kg-genotypes/ga4gh_flat

hadoop jar ~/quince/target/quince-0.0.1-SNAPSHOT-job.jar \
    com.cloudera.science.quince.LoadVariantsTool \
    -D mapreduce.map.java.opts="-Xmx3g" \
    -D mapreduce.reduce.java.opts="-Xmx3g" \
    -D mapreduce.map.memory.mb=4096 \
    -D mapreduce.reduce.memory.mb=4096 \
    --sample-group 1kg \
    --data-model GA4GH \
    hdfs:///user/ec2-user/1kg-genotypes/raw \
    hdfs:///user/ec2-user/1kg-genotypes/ga4gh
hadoop distcp \
    hdfs:///user/ec2-user/1kg-genotypes/ga4gh \
    s3n://bdg-eggo/1kg-genotypes/ga4gh
hadoop fs -rm -r hdfs:///user/ec2-user/1kg-genotypes/ga4gh

hadoop jar ~/quince/target/quince-0.0.1-SNAPSHOT-job.jar \
    com.cloudera.science.quince.LoadVariantsTool \
    -D mapreduce.map.java.opts="-Xmx3g" \
    -D mapreduce.reduce.java.opts="-Xmx3g" \
    -D mapreduce.map.memory.mb=4096 \
    -D mapreduce.reduce.memory.mb=4096 \
    --sample-group 1kg \
    --data-model ADAM \
    --flatten \
    hdfs:///user/ec2-user/1kg-genotypes/raw \
    hdfs:///user/ec2-user/1kg-genotypes/adam_flat
hadoop distcp \
    hdfs:///user/ec2-user/1kg-genotypes/adam_flat \
    s3n://bdg-eggo/1kg-genotypes/adam_flat
hadoop fs -rm -r hdfs:///user/ec2-user/1kg-genotypes/adam_flat

hadoop jar ~/quince/target/quince-0.0.1-SNAPSHOT-job.jar \
    com.cloudera.science.quince.LoadVariantsTool \
    -D mapreduce.map.java.opts="-Xmx3g" \
    -D mapreduce.reduce.java.opts="-Xmx3g" \
    -D mapreduce.map.memory.mb=4096 \
    -D mapreduce.reduce.memory.mb=4096 \
    --sample-group 1kg \
    --data-model ADAM \
    hdfs:///user/ec2-user/1kg-genotypes/raw \
    hdfs:///user/ec2-user/1kg-genotypes/adam
hadoop distcp \
    hdfs:///user/ec2-user/1kg-genotypes/adam \
    s3n://bdg-eggo/1kg-genotypes/adam
hadoop fs -rm -r hdfs:///user/ec2-user/1kg-genotypes/adam



# DELETE  DELETE  DELETE  DELETE  DELETE  DELETE  DELETE  DELETE  DELETE
# vvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvv

# ADAM PROCESSING
# convert to ADAM format
~/adam/bin/adam-submit --master yarn-client --driver-memory 8g \
    --num-executors $TOTAL_EXECUTORS --executor-cores $CORES_PER_EXECUTOR \
    --executor-memory $MEMORY_PER_EXECUTOR \
    -- \
    vcf2adam \
    -parquet_compression_codec SNAPPY \
    hdfs:///user/ec2-user/1kg-genotypes/raw \
    hdfs:///user/ec2-user/1kg-genotypes/adam_variants

# flatten parquet data
~/adam/bin/adam-submit --master yarn-client --driver-memory 8g \
    --num-executors $TOTAL_EXECUTORS --executor-cores $CORES_PER_EXECUTOR \
    --executor-memory $MEMORY_PER_EXECUTOR \
    -- \
    flatten \
    -parquet_compression_codec SNAPPY \
    hdfs:///user/ec2-user/1kg-genotypes/adam_variants \
    hdfs:///user/ec2-user/1kg-genotypes/adam_flat_variants


# locus partition parquet data with Hive
# TODO: the following query is manually crafted bc CREATE TABLE LIKE PARQUET chokes on ENUM
SEGMENT_SIZE=1000000
NUM_REDUCERS=300
TABLE_SCHEMA='`variantErrorProbability` INT, `contig__contigName` STRING, `contig__contigLength` BIGINT, `contig__contigMD5` STRING, `contig__referenceURL` STRING, `contig__assembly` STRING, `contig__species` STRING, `contig__referenceIndex` INT, `start` BIGINT, `end` BIGINT, `referenceAllele` STRING, `alternateAllele` STRING, `svAllele__type` BINARY, `svAllele__assembly` STRING, `svAllele__precise` BOOLEAN, `svAllele__startWindow` INT, `svAllele__endWindow` INT, `isSomatic` BOOLEAN'
hive -e "CREATE EXTERNAL TABLE prepartition ($TABLE_SCHEMA) STORED AS PARQUET LOCATION 'hdfs:///user/ec2-user/1kg-genotypes/adam_flat_variants'"
hive -e "CREATE EXTERNAL TABLE postpartition ($TABLE_SCHEMA) PARTITIONED BY (chr STRING, pos BIGINT) STORED AS PARQUET LOCATION 'hdfs:///user/ec2-user/1kg-genotypes/adam_flat_variants_locuspart'"
export HIVE_OPTS="--hiveconf mapreduce.job.reduces=$NUM_REDUCERS --hiveconf mapreduce.map.memory.mb=8192 --hiveconf mapreduce.reduce.memory.mb=8192 --hiveconf mapreduce.reduce.java.opts=-Xmx8192m --hiveconf hive.exec.dynamic.partition.mode=nonstrict --hiveconf hive.exec.max.dynamic.partitions=3000"
hive  -e "INSERT OVERWRITE TABLE postpartition PARTITION (chr, pos) SELECT *, contig__contigName, floor(start / $SEGMENT_SIZE) * $SEGMENT_SIZE FROM prepartition DISTRIBUTE BY contig__contigName, floor(start / $SEGMENT_SIZE) * $SEGMENT_SIZE"
hive -e "DROP TABLE prepartition"
hive -e "DROP TABLE postpartition"


# QUINCE PROCESSING
hadoop jar target/quince-0.0.1-SNAPSHOT-job.jar \
    com.cloudera.science.quince.LoadVariantsTool \
    -D mapreduce.map.java.opts="-Djava.net.preferIPv4Stack=true -Xmx3g" \
    -D mapreduce.reduce.java.opts="-Djava.net.preferIPv4Stack=true -Xmx3g" \
    -D mapreduce.map.memory.mb=4096 \
    -D mapreduce.reduce.memory.mb=4096 \
    --sample-group 1kg \
    hdfs:///user/ec2-user/1kg-genotypes/raw \
    hdfs:///user/ec2-user/1kg-genotypes/ga4gh_flat_variants_locuspart


# TRANSFER TO S3
hadoop distcp \
    hdfs:///user/ec2-user/1kg-genotypes/adam_flat_variants_locuspart \
    s3n://bdg-eggo/1kg-genotypes_adam_flat

hadoop distcp \
    hdfs:///user/ec2-user/1kg-genotypes/ga4gh_flat_variants_locuspart \
    s3n://bdg-eggo/1kg-genotypes_ga4gh_flat
