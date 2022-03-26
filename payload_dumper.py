#!/usr/bin/env python
import struct
import hashlib
import bz2
import sys
import argparse
import bsdiff4
import io
import os
try:
    import lzma
except ImportError:
    from backports import lzma

import update_metadata_pb2 as um

flatten = lambda l: [item for sublist in l for item in sublist]

def u32(x):
    return struct.unpack('>I', x)[0]

def u64(x):
    return struct.unpack('>Q', x)[0]

def verify_contiguous(exts):
    blocks = 0

    for ext in exts:
        if ext.start_block != blocks:
            return False

        blocks += ext.num_blocks

    return True

def data_for_op(op, out_file, old_file):
    global chunk_count
    global last_chunk_count
    global data_last_chunk
    global data_last_offset
    global payloadfile_chunk

    # sys.stdout.write("offset: %s, length: %s vs (%d), outfile: \n" % (str(data_offset + op.data_offset), str(op.data_length), 4 * 1024 * 1024 * 1024))
    offset = data_offset + op.data_offset

    if chunk_count > last_chunk_count:
        sys.stdout.write("XX: hit %d\n" % (offset))
        last_chunk_count += 1

        chunk = ""

        # drop first chunk
        if chunk_count == 1:
            chunk = payloadfile_dup.read(chunk_size)

        chunk = payloadfile_dup.read(chunk_size)
        payloadfile_chunk = open("/tmp/chunk.%d" % chunk_count, 'wb')
        payloadfile_chunk.write(chunk)
        payloadfile_chunk.close()

        payloadfile_chunk = open("/tmp/chunk.%d" % chunk_count, 'rb')
        payloadfile_chunk.seek(offset)
        data = data_last_chunk + payloadfile_chunk.read(op.data_length -
                                                        (chunk_size - data_last_offset))

        data_last_chunk = ""
        data_last_offset = 0
    elif chunk_count > 0:
        payloadfile_chunk.seek(offset)
        data = payloadfile_chunk.read(op.data_length)

    else:
        args.payloadfile.seek(offset)
        data = args.payloadfile.read(op.data_length)

    if offset < chunk_size and offset + op.data_length > chunk_size:
        data_last_chunk = data
        data_last_offset = offset
        chunk_count += 1
        return

    # assert hashlib.sha256(data).digest() == op.data_sha256_hash, 'operation data hash mismatch'

    # sys.stdout.write("XX: data_offset: %d, data_length: %d\n" % (offset, op.data_length))
    # sys.stdout.flush()

    if op.type == op.REPLACE_XZ:
        dec = lzma.LZMADecompressor()
        data = dec.decompress(data)
        out_file.seek(op.dst_extents[0].start_block*block_size)
        out_file.write(data)
    elif op.type == op.REPLACE_BZ:
        dec = bz2.BZ2Decompressor()
        data = dec.decompress(data)
        out_file.seek(op.dst_extents[0].start_block*block_size)
        out_file.write(data)
    elif op.type == op.REPLACE:
        out_file.seek(op.dst_extents[0].start_block*block_size)
        out_file.write(data)
    elif op.type == op.SOURCE_COPY:
        if not args.diff:
            print ("SOURCE_COPY supported only for differential OTA")
            sys.exit(-2)
        out_file.seek(op.dst_extents[0].start_block*block_size)
        for ext in op.src_extents:
            old_file.seek(ext.start_block*block_size)
            data = old_file.read(ext.num_blocks*block_size)
            out_file.write(data)
    elif op.type == op.SOURCE_BSDIFF:
        if not args.diff:
            print ("SOURCE_BSDIFF supported only for differential OTA")
            sys.exit(-3)
        out_file.seek(op.dst_extents[0].start_block*block_size)
        tmp_buff = io.BytesIO()
        for ext in op.src_extents:
            old_file.seek(ext.start_block*block_size)
            old_data = old_file.read(ext.num_blocks*block_size)
            tmp_buff.write(old_data)
        tmp_buff.seek(0)
        old_data = tmp_buff.read()
        tmp_buff.seek(0)
        tmp_buff.write(bsdiff4.patch(old_data, data))
        n = 0;
        tmp_buff.seek(0)
        for ext in op.dst_extents:
            tmp_buff.seek(n*block_size)
            n += ext.num_blocks
            data = tmp_buff.read(ext.num_blocks*block_size)
            out_file.seek(ext.start_block*block_size)
            out_file.write(data)
    elif op.type == op.ZERO:
        for ext in op.dst_extents:
            out_file.seek(ext.start_block*block_size)
            out_file.write(b'\x00' * ext.num_blocks*block_size)
    else:
        print ("Unsupported type = %d" % op.type)
        sys.exit(-1)

    return data


def dump_part(part):
    # global chunk_count
    # global last_chunk_count
    # global data_last_chunk
    # global data_last_offset
    # global payloadfile_chunk

    sys.stdout.write("Processing %s partition" % part.partition_name)
    sys.stdout.flush()

    # # XX: for debug
    # if part.partition_name != 'my_heytap':
    #     sys.stdout.write("\nSkip %s partition\n" % part.partition_name)
    #     sys.stdout.flush()
    #     return

    out_file = open('%s/%s.img' % (args.out, part.partition_name), 'wb')
    h = hashlib.sha256()
    # sys.stdout.write("XX: outfile_name: %s/%s.img\n" % (args.out, part.partition_name))

    if args.diff:
        old_file = open('%s/%s.img' % (args.old, part.partition_name), 'rb')
    else:
        old_file = None

    for op in part.operations:
        data = data_for_op(op,out_file,old_file)
        sys.stdout.write(".")
        sys.stdout.flush()

    # last_chunk_count = 0
    # chunk_count = 0
    # data_last_chunk = ""
    # data_last_offset = 0
    # payloadfile_chunk = None

    print("Done")


parser = argparse.ArgumentParser(description='OTA payload dumper')
parser.add_argument('payloadfile', type=argparse.FileType('rb'),
                    help='payload file name')
parser.add_argument('--out', default='output',
                    help='output directory (defaul: output)')
parser.add_argument('--diff',action='store_true',
                    help='extract differential OTA, you need put original images to old dir')
parser.add_argument('--old', default='old',
                    help='directory with original images for differential OTA (defaul: old)')
parser.add_argument('--images', default="",
                    help='images to extract (default: empty)')
args = parser.parse_args()

#Check for --out directory exists
if not os.path.exists(args.out):
    os.makedirs(args.out)

payloadfile_dup = args.payloadfile
magic = args.payloadfile.read(4)
assert magic == b'CrAU'

file_format_version = u64(args.payloadfile.read(8))
assert file_format_version == 2

manifest_size = u64(args.payloadfile.read(8))

metadata_signature_size = 0

if file_format_version > 1:
    metadata_signature_size = u32(args.payloadfile.read(4))

manifest = args.payloadfile.read(manifest_size)
metadata_signature = args.payloadfile.read(metadata_signature_size)

data_offset = args.payloadfile.tell()
data_last_chunk = ""
data_last_offset = 0
chunk_size = 4 * 1024 * 1024 * 1024  # 1G
chunk_count = 0
last_chunk_count = 0
payloadfile_chunk = None

dam = um.DeltaArchiveManifest()
dam.ParseFromString(manifest)
block_size = dam.block_size

if args.images == "":
    for part in dam.partitions:
        dump_part(part)
else:
    images = args.images.split(",")
    for image in images:
        partition = [part for part in dam.partitions if part.partition_name == image]
        if partition:
            dump_part(partition[0])
        else:
            sys.stderr.write("Partition %s not found in payload!\n" % image)

