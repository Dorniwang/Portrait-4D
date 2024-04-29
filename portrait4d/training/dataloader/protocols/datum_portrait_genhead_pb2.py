# Generated by the protocol buffer compiler.  DO NOT EDIT!
# source: datum_portrait.proto

import sys
_b=sys.version_info[0]<3 and (lambda x:x) or (lambda x:x.encode('latin1'))
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from google.protobuf import reflection as _reflection
from google.protobuf import symbol_database as _symbol_database
from google.protobuf import descriptor_pb2
# @@protoc_insertion_point(imports)

_sym_db = _symbol_database.Default()




DESCRIPTOR = _descriptor.FileDescriptor(
  name='datum_portrait.proto',
  package='',
  syntax='proto2',
  serialized_pb=_b('\n\x14\x64\x61tum_portrait.proto\"\xb6\x01\n\x05\x44\x61tum\x12\x10\n\x08\x63hannels\x18\x01 \x01(\x05\x12\x0e\n\x06height\x18\x02 \x01(\x05\x12\r\n\x05width\x18\x03 \x01(\x05\x12\x0b\n\x03num\x18\x04 \x01(\x05\x12\x0e\n\x06images\x18\x05 \x01(\x0c\x12\x0c\n\x04segs\x18\x06 \x01(\x0c\x12\x10\n\x08\x66\x65\x61tures\x18\x07 \x01(\x0c\x12\x0e\n\x06\x64\x65pths\x18\x08 \x01(\x0c\x12\x11\n\ttriplanes\x18\t \x01(\x0c\x12\x0e\n\x06labels\x18\n \x01(\x0c\x12\x0c\n\x04mots\x18\x0b \x01(\x0c')
)
_sym_db.RegisterFileDescriptor(DESCRIPTOR)




_DATUM = _descriptor.Descriptor(
  name='Datum',
  full_name='Datum',
  filename=None,
  file=DESCRIPTOR,
  containing_type=None,
  fields=[
    _descriptor.FieldDescriptor(
      name='channels', full_name='Datum.channels', index=0,
      number=1, type=5, cpp_type=1, label=1,
      has_default_value=False, default_value=0,
      message_type=None, enum_type=None, containing_type=None,
      is_extension=False, extension_scope=None,
      options=None),
    _descriptor.FieldDescriptor(
      name='height', full_name='Datum.height', index=1,
      number=2, type=5, cpp_type=1, label=1,
      has_default_value=False, default_value=0,
      message_type=None, enum_type=None, containing_type=None,
      is_extension=False, extension_scope=None,
      options=None),
    _descriptor.FieldDescriptor(
      name='width', full_name='Datum.width', index=2,
      number=3, type=5, cpp_type=1, label=1,
      has_default_value=False, default_value=0,
      message_type=None, enum_type=None, containing_type=None,
      is_extension=False, extension_scope=None,
      options=None),
    _descriptor.FieldDescriptor(
      name='num', full_name='Datum.num', index=3,
      number=4, type=5, cpp_type=1, label=1,
      has_default_value=False, default_value=0,
      message_type=None, enum_type=None, containing_type=None,
      is_extension=False, extension_scope=None,
      options=None),
    _descriptor.FieldDescriptor(
      name='images', full_name='Datum.images', index=4,
      number=5, type=12, cpp_type=9, label=1,
      has_default_value=False, default_value=_b(""),
      message_type=None, enum_type=None, containing_type=None,
      is_extension=False, extension_scope=None,
      options=None),
    _descriptor.FieldDescriptor(
      name='segs', full_name='Datum.segs', index=5,
      number=6, type=12, cpp_type=9, label=1,
      has_default_value=False, default_value=_b(""),
      message_type=None, enum_type=None, containing_type=None,
      is_extension=False, extension_scope=None,
      options=None),
    _descriptor.FieldDescriptor(
      name='features', full_name='Datum.features', index=6,
      number=7, type=12, cpp_type=9, label=1,
      has_default_value=False, default_value=_b(""),
      message_type=None, enum_type=None, containing_type=None,
      is_extension=False, extension_scope=None,
      options=None),
    _descriptor.FieldDescriptor(
      name='depths', full_name='Datum.depths', index=7,
      number=8, type=12, cpp_type=9, label=1,
      has_default_value=False, default_value=_b(""),
      message_type=None, enum_type=None, containing_type=None,
      is_extension=False, extension_scope=None,
      options=None),
    _descriptor.FieldDescriptor(
      name='triplanes', full_name='Datum.triplanes', index=8,
      number=9, type=12, cpp_type=9, label=1,
      has_default_value=False, default_value=_b(""),
      message_type=None, enum_type=None, containing_type=None,
      is_extension=False, extension_scope=None,
      options=None),
    _descriptor.FieldDescriptor(
      name='labels', full_name='Datum.labels', index=9,
      number=10, type=12, cpp_type=9, label=1,
      has_default_value=False, default_value=_b(""),
      message_type=None, enum_type=None, containing_type=None,
      is_extension=False, extension_scope=None,
      options=None),
    _descriptor.FieldDescriptor(
      name='mots', full_name='Datum.mots', index=10,
      number=11, type=12, cpp_type=9, label=1,
      has_default_value=False, default_value=_b(""),
      message_type=None, enum_type=None, containing_type=None,
      is_extension=False, extension_scope=None,
      options=None),
  ],
  extensions=[
  ],
  nested_types=[],
  enum_types=[
  ],
  options=None,
  is_extendable=False,
  syntax='proto2',
  extension_ranges=[],
  oneofs=[
  ],
  serialized_start=25,
  serialized_end=207,
)

DESCRIPTOR.message_types_by_name['Datum'] = _DATUM

Datum = _reflection.GeneratedProtocolMessageType('Datum', (_message.Message,), dict(
  DESCRIPTOR = _DATUM,
  __module__ = 'datum_portrait_genhead_pb2'
  # @@protoc_insertion_point(class_scope:Datum)
  ))
_sym_db.RegisterMessage(Datum)


# @@protoc_insertion_point(module_scope)
