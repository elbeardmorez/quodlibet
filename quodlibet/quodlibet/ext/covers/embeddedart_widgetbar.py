# -*- coding: utf-8 -*-
# Copyright 2017 Pete Beardmore
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License version 2 as
# published by the Free Software Foundation
import os
import hashlib
from senf import path2fsn
from gi.repository import Gtk, Gdk, GLib

from quodlibet import _
from quodlibet.plugins import PluginConfig, BoolConfProp
from quodlibet.plugins.events import EventPlugin
from quodlibet.plugins.gui import UserInterfacePlugin
from quodlibet.formats import EmbeddedImage
from quodlibet.qltk import Icons, add_global_css
from quodlibet.qltk.widgetbar import WidgetBar
from quodlibet.qltk.x import Align
from quodlibet.util import print_d
from quodlibet.qltk.cover import CoverImage
from quodlibet.util.thumbnails import get_thumbnail_folder
from quodlibet.qltk.ccb import ConfigCheckButton


plugin_id = "embeddedartwidgetbar"


class Config(object):
    _config = PluginConfig(plugin_id)

    expanded = BoolConfProp(_config, "expanded", True)
    name_in_label = BoolConfProp(_config, "name_in_label", True)
    size_in_label = BoolConfProp(_config, "size_in_label", False)
    size_in_tooltip = BoolConfProp(_config, "size_in_tooltip", True)


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


class EmbeddedArtBox(Gtk.HBox):

    def __init__(self):
        super(EmbeddedArtBox, self).__init__()

        self.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)

        # covers stack
        self.covers = []
        self.covers_max = 50

    def update(self, songs):
        if not songs:
            return

        self.__clear_covers()
        for song in songs:
            imageitems = self.__get_artwork(song)
            if len(self.covers) == self.covers_max:
                print_d("covers max hit, ignoring then rest!")
                break
            self.__generate_covers(imageitems, song)

    def __clear_covers(self):
        self.covers = []
        for w in self.get_children():
            self.remove(w)

    def __generate_covers(self, imageitems, song):
        covers = len(self.covers)
        if len(self.covers) + len(imageitems) >= self.covers_max:
            if self.covers < self.covers_max:
                imageitems = imageitems[:self.covers_max - covers]

        for image in imageitems:

            title = "<b>" + GLib.markup_escape_text(image.album) + "</b>"
            name = os.path.splitext(os.path.basename(image.name))[0] \
                       .split('_')[-1]
            size = "x".join([str(image.width), str(image.height)])

            coverimage = CoverImage(resize=True)
            coverimage.set_song(song)

            fsn = path2fsn(image.name)
            fo = open(fsn, "rb")
            coverimage.set_image(fo, name, image.external)

            coverimage_box = Gtk.VBox()
            coverimage_box.image = coverimage
            coverimage_box.image_title = title
            coverimage_box.image_name = name
            coverimage_box.image_size = size

            coverimage_box.pack_start(coverimage, True, True, 2)

            coverimage_box_hborder = Gtk.HBox()
            coverimage_box_hborder.pack_start(
                coverimage_box, True, True, 3)
            coverimage_box_vborder = Gtk.VBox()
            coverimage_box_vborder.pack_start(
                coverimage_box_hborder, True, True, 3)

            coverimage_box_outer = Gtk.EventBox()
            coverimage_box_outer.add(coverimage_box_vborder)

            def highlight_toggle(widget, event, *data):
                scv = coverimage_box_vborder.get_style_context()
                sch = coverimage_box_hborder.get_style_context()
                if scv.has_class('highlightbox'):
                    scv.remove_class('highlightbox')
                    sch.remove_class('highlightbox')
                else:
                    scv.add_class('highlightbox')
                    sch.add_class('highlightbox')

            coverimage_box_outer.connect(
                "button-press-event", highlight_toggle)

            tooltip = []
            if CONFIG.size_in_tooltip:
                tooltip.append(size)
            if tooltip:
                coverimage.set_tooltip_markup('\n'.join(tooltip))

            desc = str.format("%s%s%s") % (
                title,
                " [" + name + "]" if CONFIG.name_in_label else "",
                " [" + size + "]" if CONFIG.size_in_label else "")
            label_desc = Gtk.Label(desc)
            label_desc.set_line_wrap(True)
            label_desc.set_use_markup(True)
            label_desc.set_tooltip_markup(image.path)
            coverimage_box.label = label_desc
            coverimage_box.pack_start(label_desc, False, True, 4)

            coverimage_box_outer_align = Align(bottom=15)
            coverimage_box_outer_align.add(coverimage_box_outer)
            coverimage_box_outer_align.coverimage_box = coverimage_box
            self.pack_start(coverimage_box_outer_align, True, False, 5)

            self.covers.append(image)

        self.show_all()

    def __get_artwork(self, song):
        # generate art set for path

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
                path_hash = hashlib.md5(pathfile.encode("utf8")).hexdigest()
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
                    types[itype] = True
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
        for widget in self.get_children():
            box = widget.coverimage_box
            box.label.set_text("%s%s%s" % (
                box.image_title,
                " [" + box.image_name + "]"
                if CONFIG.name_in_label else "",
                " [" + box.image_size + "]"
                if CONFIG.size_in_label else ""))
            box.label.set_use_markup(True)

    def update_tooltips(self):
        for widget in self.get_children():
            box = widget.coverimage_box
            box.image.set_tooltip_markup(None)
            tooltip = []
            if CONFIG.size_in_tooltip:
                tooltip.append(box.image_size)
            if tooltip:
                box.image.set_tooltip_markup('\n'.join(tooltip))


class EmbeddedArtWidgetBarPlugin(UserInterfacePlugin, EventPlugin):
    """The plugin class."""

    PLUGIN_ID = plugin_id
    PLUGIN_NAME = _("Embedded Art Widget Bar")
    PLUGIN_DESC = _("Display embedded art.")
    PLUGIN_CONFIG_SECTION = __name__
    PLUGIN_ICON = Icons.INSERT_IMAGE

    def __init__(self):
        super(EmbeddedArtWidgetBarPlugin, self).__init__()
        self.live = False

        add_global_css("""
            .highlightbox {
                border-color: #558fcb;
                border-radius: 2px;
            }
        """)

    def enabled(self):
        pass

    def disabled(self):
        # save data
        self.__save()

    def create_widgetbar(self):
        self.__widgetbar = WidgetBar(plugin_id)
        self.__content = self.__widgetbar.box
        self.__widgetbar.title.set_text(self.PLUGIN_NAME)

        align_covers = Gtk.Alignment(xalign=0.5, xscale=1.0)
        self.__embeddedart_box = EmbeddedArtBox()
        align_covers.add(self.__embeddedart_box)
        self.__content.pack_start(align_covers, True, True, 0)
        self.__content.show_all()

        self.live = True

        return self.__widgetbar

    def plugin_on_songs_selected(self, songs):
        if not self.live:
            return
        self.__embeddedart_box.update(songs)

    def __save(self):
        print_d("saving config data")

    def PluginPreferences(self, window):

        box = Gtk.VBox(spacing=4)

        # toggles
        toggles = [
            (plugin_id + '_name_in_label', _("Show image name in label"),
             None, False, lambda *x: self.__embeddedart_box.update_labels()),
            (plugin_id + '_size_in_label', _("Show image size in label"),
             None, False, lambda *x: self.__embeddedart_box.update_labels()),
            (plugin_id + '_size_in_tooltip', _("Show image size in tooltip"),
             None, True, lambda *x: self.__embeddedart_box.update_tooltips()),
        ]

        for key, label, tooltip, default, changed_cb in toggles:
            ccb = ConfigCheckButton(label, 'plugins', key,
                                    populate=True)
            ccb.connect("toggled", changed_cb)
            if tooltip:
                ccb.set_tooltip_text(tooltip)

            box.pack_start(ccb, True, True, 0)

        return box
