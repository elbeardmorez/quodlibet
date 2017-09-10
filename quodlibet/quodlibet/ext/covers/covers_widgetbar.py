# -*- coding: utf-8 -*-
# Copyright 2017 Pete Beardmore
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License version 2 as
# published by the Free Software Foundation

import os
import glob
import hashlib
from itertools import groupby
from operator import itemgetter
from senf import path2fsn
from gi.repository import Gtk, Gdk, GLib

import quodlibet
from quodlibet import _
from quodlibet import config
from quodlibet.plugins import PluginConfig, IntConfProp, BoolConfProp
from quodlibet.plugins.events import EventPlugin
from quodlibet.plugins.gui import UserInterfacePlugin
from quodlibet.formats import MusicFile, EmbeddedImage
from quodlibet.qltk import Icons
from quodlibet.qltk.widgetbar import WidgetBar
from quodlibet.qltk.x import Align
from quodlibet.util import print_d
from quodlibet.pattern import ArbitraryExtensionFileFromPattern
from quodlibet.qltk.cover import CoverImage
from quodlibet.util.thumbnails import get_thumbnail_folder
from quodlibet.qltk.ccb import ConfigCheckButton


plugin_id = "coverswidgetbar"


SONGS_HISTORY_SET = \
    os.path.join(quodlibet.get_user_dir(),
                 "lists", "coverswidgetbarhistory.default")


class Config(object):
    _config = PluginConfig(plugin_id)

    expanded = BoolConfProp(_config, "expanded", True)
    images_max = IntConfProp(_config, "images_max", 50)
    songs_save = IntConfProp(_config, "songs_save", 10)
    ignore_in_last = IntConfProp(_config, "ignore_in_last", 1)
    follow_front = BoolConfProp(_config, "follow_front", True)
    name_in_label = BoolConfProp(_config, "name_in_label", True)
    size_in_label = BoolConfProp(_config, "size_in_label", False)
    size_in_tooltip = BoolConfProp(_config, "size_in_tooltip", True)
    uri_in_tooltip = BoolConfProp(_config, "uri_in_tooltip", True)
    songs_history = []


CONFIG = Config()


class ImageItem(object):
    def __init__(self, name, path, artist, album, width, height, external):
        self.name = name
        self.path = path
        self.artist = artist
        self.album = album
        self.width = width
        self.height = height
        self.external = external

    def key(self):
        return "|".join([self.artist, self.album, str(self.external)])


class CoversBox(Gtk.HBox):

    def __init__(self):
        super(CoversBox, self).__init__()

        self.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)

        # covers stack
        self.covers = []

        self.__artsets_last = []

        # restore history
        if len(CONFIG.songs_history) > 0:
            for f in CONFIG.songs_history:
                song = self.get_song(f)
                if song:
                    self.generate_covers(self.get_artwork(song), song)

    def new_song(self, song):
        self.update(song)

    def update(self, song):
        # add cover

        if not song:
            return

        imageitems = self.get_artwork(song)
        self.generate_covers(imageitems, song)

    def get_song(self, pathfile):
        # get song object from file
        song = None
        try:
            song = MusicFile(pathfile)
        except:
            print_d("couldn't get song from file: %r" % pathfile)
        return song

    def generate_covers(self, imageitems, song):
        art_last = [artitem for artset in self.__artsets_last
                            for artitem in artset]

        for image in reversed(imageitems):
            if image.key() in art_last:
                continue

            title = "<b>" + GLib.markup_escape_text(image.album) + "</b>"
            name = os.path.splitext(os.path.basename(image.name))[0] \
                       .split('_')[-1]
            size = "x".join([str(image.width), str(image.height)])
            uri = GLib.markup_escape_text(("external: " + image.name)
                                          if image.external
                                          else ("internal: " + name))

            coverimage = CoverImage(
                size_mode=Gtk.SizeRequestMode.WIDTH_FOR_HEIGHT)
            coverimage.set_song(song)

            fsn = path2fsn(image.name)
            fo = open(fsn, "rb")
            coverimage.set_image(fo, name, image.external)

            coverimage_box = Gtk.VBox()
            coverimage_box.image = coverimage
            coverimage_box.image_title = title
            coverimage_box.image_name = name
            coverimage_box.image_size = size
            coverimage_box.image_uri = uri

            coverimage_box.pack_start(coverimage, True, True, 0)

            tooltip = []
            if CONFIG.size_in_tooltip:
                tooltip.append(size)
            if CONFIG.uri_in_tooltip:
                tooltip.append(uri)
            if tooltip:
                coverimage.set_tooltip_markup('\n'.join(tooltip))

            desc = str.format("%s%s%s") % (
                title,
                " [" + name + "]" if CONFIG.name_in_label else "",
                " [" + size + "]" if CONFIG.size_in_label else "")

            label_desc = Gtk.Label(desc)
            label_desc.set_line_wrap(True)
            label_desc.set_use_markup(True)
            align_label_desc = Align(bottom=15)
            align_label_desc.add(label_desc)
            coverimage_box.label = label_desc
            coverimage_box.pack_start(align_label_desc, False, True, 4)

            self.pack_end(coverimage_box, True, True, 5)

            self.covers.append(image)

        while len(self.covers) > CONFIG.images_max:
            self.covers.pop()
            self.remove(self.get_children()[-1])

        if imageitems:
            self.__artsets_last.append(map(lambda ii: ii.key(), imageitems))
            diff = len(self.__artsets_last) - CONFIG.ignore_in_last
            if diff > 0:
                self.__artsets_last = self.__artsets_last[diff:]

        self.show_all()

    def get_artwork(self, song):
        # generate art set for path

        IMAGE_EXTENSIONS = ['jpg', 'jpeg', 'png', 'bmp']

        imageitems = []
        pathfile = song['~filename']
        album = "" if 'album' not in song else song['album']
        artist = ""
        if 'performer' in song:
            artist = song['performer']
        elif 'albumartist' in song:
            artist = song['albumartist']
        elif 'album artist' in song:
            artist = song['album artist']
        elif 'artist' in song:
            artist = song['artist']

        # external
        path = os.path.dirname(song['~filename'])
        images = []

        def glob_escape(s):
            for c in ['[', '*', '?']:
                s = s.replace(c, '[' + c + ']')
            return s

        path_escaped = glob_escape(path)
        for suffix in IMAGE_EXTENSIONS:
            images.extend(glob.glob(os.path.join(path_escaped, "*." + suffix)))
        images_match = []
        if len(images) > 0:
            filenames = config.getstringlist("albumart", "search_filenames")
            for fn in filenames:
                fn = os.path.join(path, fn)
                if "<" in fn:
                    # resolve path
                    fnres = ArbitraryExtensionFileFromPattern(fn).format(song)
                    if fnres in images and fnres not in images_match:
                        images_match.append(fnres)
                elif "*" in fn:
                    images_match.extend(f for f in glob.glob(fn)
                                     if f in images and f not in images_match)
                elif fn in images and fn not in images_match:
                    images_match.append(fn)
            if len(images_match) > 0:
                # build imageitem
                for f in images_match:
                    width, height = self.get_info(f)
                    imageitems.append(ImageItem(f, pathfile, artist, album,
                                                width, height, True))

        # internal
        if song.has_images:
            # dump images to cache
            images = song.get_images()
            path_thumbs = os.path.join(get_thumbnail_folder(), plugin_id)
            if not os.path.exists(path_thumbs):
                try:
                    #~/.cache/quodlibet/covers/
                    os.makedirs(path_thumbs)
                    os.chmod(path_thumbs, 0o600)
                    print_d("created art cache path for %s at %r"
                             % (plugin_id, path_thumbs))
                except:
                    print_d("failed setting up art cache path for %s at %r"
                             % (plugin_id, path_thumbs))

            if os.path.exists(path_thumbs):
                types = {}
                path_hash = hashlib.md5(pathfile.encode("ascii")).hexdigest()
                # ignore some mime-types, links..
                mime_ignore = ['-->']
                for i in images:
                    if i.mime_type in mime_ignore:
                        continue
                    itype = self.clean_embedded_art_type(str(i.type))
                    if itype in types:
                        suffix = 2
                        while itype + str(suffix) in types:
                            suffix += 1
                        itype = itype + str(suffix)
                    key = path_hash + "_" + itype
                    f = os.path.join(path_thumbs, key)

                    dump = False
                    if os.path.exists(f):
                        fo = open(f, 'rb')
                        if fo.tell() != i.size():
                            dump = True
                        elif not self.__file_equals_embeddedimage(fo, i):
                            dump = True
                    else:
                        dump = True
                    if dump and not self.dump_file(i, f):
                        continue

                    width, height = self.get_info(f)
                    imageitems.append(ImageItem(f, pathfile, artist, album,
                                                width, height, False))

        return imageitems

    def clean_embedded_art_type(self, s):
        return s.replace("PictureType", "").strip('. ').lower()

    def get_info(self, pathfile):
        image = EmbeddedImage.from_path(pathfile)
        if image:
            return image.width, image.height
        else:
            return "-", "-"

    def dump_file(self, data, pathfile):
        fsn = path2fsn(pathfile)
        success = True
        try:
            fo = open(fsn, "wb")
            fo.write(data.read())
            fo.close()
        except:
            success = False

        return success

    def __file_equals_embeddedimage(self, fo, i, bytes_to_compare=512):

        # why twice? no idea. once doesn't work here though
        fo.seek(-min(bytes_to_compare, fo.tell()), 2)
        fo.seek(-min(bytes_to_compare, fo.tell()), 2)
        fbytes = fo.read()
        ibytes = i.read(-bytes_to_compare)

        return fbytes == ibytes

    def update_labels(self):
        for box in self.get_children():
            box.label.set_text("%s%s%s" % (
                box.image_title,
                " [" + box.image_name + "]" if CONFIG.name_in_label else "",
                " [" + box.image_size + "]" if CONFIG.size_in_label else ""))
            box.label.set_use_markup(True)

    def update_tooltips(self):
        for box in self.get_children():
            box.image.set_tooltip_markup(None)
            tooltip = []
            if CONFIG.size_in_tooltip:
                tooltip.append(box.image_size)
            if CONFIG.uri_in_tooltip:
                tooltip.append(box.image_uri)
            if tooltip:
                box.image.set_tooltip_markup('\n'.join(tooltip))


class CoversWidgetBarPlugin(UserInterfacePlugin, EventPlugin):
    """The plugin class."""

    PLUGIN_ID = plugin_id
    PLUGIN_NAME = _("Covers Widget Bar")
    PLUGIN_DESC = _("Display all covers found for playing tracks.")
    PLUGIN_CONFIG_SECTION = __name__
    PLUGIN_ICON = Icons.INSERT_IMAGE

    def __init__(self):
        super(CoversWidgetBarPlugin, self).__init__()
        self.live = False

    def enabled(self):
        # setup
        self.__read_songs_history()

    def disabled(self):
        # save data
        self.__save()

    def create_widgetbar(self):
        self.__widgetbar = WidgetBar(plugin_id)
        self.__content = self.__widgetbar.box
        self.__widgetbar.title.set_text(self.PLUGIN_NAME)

        align_covers = Gtk.Alignment(xalign=0.5, xscale=1.0)
        self.__coversbox = CoversBox()
        align_covers.add(self.__coversbox)
        self.__content.pack_start(align_covers, True, True, 0)
        self.__content.show_all()

        self.live = True

        return self.__widgetbar

    def plugin_on_song_started(self, song):
        if not self.live:
            return
        self.__coversbox.new_song(song)
        self.__follow_front()

    def __save(self):
        print_d("saving config data")
        image_paths = \
            map(lambda ii: ii.path, self.__coversbox.covers)
        image_paths_nonconcurrent_unique = \
                 map(itemgetter(0), groupby(image_paths))[
               -1 * min(len(self.__coversbox.covers), CONFIG.songs_save):]
        CONFIG.songs_history = image_paths_nonconcurrent_unique
        self.__write_songs_history()

    def __read_songs_history(self):
        items = WidgetBar.read_datafile(SONGS_HISTORY_SET, 1)
        CONFIG.songs_history = map(lambda x: x[0], items)

    def __write_songs_history(self):
        WidgetBar.write_datafile(
            SONGS_HISTORY_SET, map(lambda x: [x], CONFIG.songs_history),
            lambda x: x)

    def __songs_save_changed(self, widget, *data):
        CONFIG.songs_save = widget.get_numeric()

    def __ignore_in_last_changed(self, widget, *data):
        CONFIG.ignore_in_last = widget.get_numeric()

    def __follow_front(self):
        if CONFIG.follow_front:
            self.__widgetbar.scroll.get_hadjustment().set_value(0)

    def PluginPreferences(self, window):

        box = Gtk.VBox(spacing=4)

        # spins
        spins = [
            (_("Max unique art sets to save in history"),
             "",
             CONFIG.songs_save, 0, 50,
             self.__songs_save_changed),
            (_("Ignore if song is in the last 'x' songs played"),
             _("note: limited by session history"),
             CONFIG.ignore_in_last, 0, CONFIG.songs_save,
             self.__ignore_in_last_changed)
        ]
        for label, tooltip, value, lower, upper, changed_cb in spins:
            spin_box = Gtk.HBox()
            spin_spin = Gtk.SpinButton(
                adjustment=Gtk.Adjustment.new(
                    value, lower, upper, 1, 10, 0), climb_rate=1, digits=0)
            spin_spin.set_numeric(True)
            if tooltip:
                spin_spin.set_tooltip_text(tooltip)
            spin_spin.connect('value-changed', changed_cb)
            spin_box.pack_start(spin_spin, False, False, 0)
            spin_label = Gtk.Label(label)
            spin_label.set_mnemonic_widget(spin_spin)
            spin_label_align = Align(left=5)
            spin_label_align.add(spin_label)
            spin_box.pack_start(spin_label_align, False, False, 0)

            box.pack_start(spin_box, True, True, 0)

        # space
        box.pack_start(Gtk.VBox(), False, True, 6)

        # toggles
        toggles = [
            (plugin_id + '_follow_front', _("Follow front of covers list"),
             None, False,
             lambda w, *x: self.__follow_front()),
            (plugin_id + '_name_in_label', _("Show image name in label"),
             None, False, lambda *x: self.__coversbox.update_labels()),
            (plugin_id + '_size_in_label', _("Show image size in label"),
             None, False, lambda *x: self.__coversbox.update_labels()),
            (plugin_id + '_size_in_tooltip', _("Show image size in tooltip"),
             None, True, lambda *x: self.__coversbox.update_tooltips()),
            (plugin_id + '_uri_in_tooltip', _("Show image uri in tooltip"),
             None, True, lambda *x: self.__coversbox.update_tooltips()),
        ]

        for key, label, tooltip, default, changed_cb in toggles:
            ccb = ConfigCheckButton(label, 'plugins', key,
                                    populate=True)
            ccb.connect("toggled", changed_cb)
            if tooltip:
                ccb.set_tooltip_text(tooltip)

            box.pack_start(ccb, True, True, 0)

        return box
