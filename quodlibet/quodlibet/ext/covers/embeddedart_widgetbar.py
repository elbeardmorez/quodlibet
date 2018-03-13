# -*- coding: utf-8 -*-
# Copyright 2017 Pete Beardmore
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.

import os
import hashlib
from itertools import groupby
from senf import path2fsn
from gi.repository import Gtk, Gdk, GLib, GObject

from quodlibet import _
from quodlibet import app
from quodlibet.plugins import PluginConfig, BoolConfProp
from quodlibet.plugins.events import EventPlugin
from quodlibet.plugins.gui import UserInterfacePlugin
from quodlibet.formats import EmbeddedImage
from quodlibet.qltk import Icons, add_global_css, add_css, gtk_version
from quodlibet.qltk.widgetbar import WidgetBar
from quodlibet.qltk.x import Align, ScrolledWindow
from quodlibet.util import print_d, print_exc
from quodlibet.qltk.cover import CoverImage
from quodlibet.util.thumbnails import get_thumbnail_folder
from quodlibet.qltk.ccb import ConfigCheckButton
from quodlibet.formats import AudioFileError
from quodlibet.qltk.chooser import choose_files, create_chooser_filter


plugin_id = "embeddedartwidgetbar"


class Config(object):
    _config = PluginConfig(plugin_id)

    expanded = BoolConfProp(_config, "expanded", True)
    name_in_label = BoolConfProp(_config, "name_in_label", True)
    size_in_label = BoolConfProp(_config, "size_in_label", False)
    size_in_tooltip = BoolConfProp(_config, "size_in_tooltip", True)
    collapsed_view = BoolConfProp(_config, "collapsed_view", False)


CONFIG = Config()
DOUBLE_CLICK_TIMEOUT = 200


class ImageItem(object):
    def __init__(self, name, path, artist, album,
                 width, height, external, data_hash):
        self.name = name
        self.path = path
        self.artist = artist
        self.album = album
        self.width = width
        self.height = height
        self.external = external
        self.data_hash = data_hash

    def key(self):
        return "|".join([self.artist, self.album, str(self.external)])


class ImageWidgetSignalBox(GObject.GObject):

    __gsignals__ = {
        "subselect-count-changed":
        (GObject.SignalFlags.RUN_LAST, None, []),
        "select-count-changed":
        (GObject.SignalFlags.RUN_LAST, None, []),
    }


class ImageWidget(Gtk.HBox):

    def __init__(self, image, song):
        super(ImageWidget, self).__init__()

        self.signalbox = ImageWidgetSignalBox()

        name = os.path.splitext(os.path.basename(image.name))[0] \
                   .split('_')[-1]
        title = "<b>" + GLib.markup_escape_text(image.album) + "</b>"
        size = "x".join([str(image.width), str(image.height)])

        coverimage = CoverImage(resize=True)
        coverimage.set_song(song)
        coverimage.cover_click_cb = self.__cover_click

        fsn = path2fsn(image.name)
        fo = open(fsn, "rb")
        coverimage.set_image(fo, name, image.external)

        self.image = image
        self.song = song
        self.nested = [self]
        self.nested_active = {self: False}
        self.cover = coverimage
        self.image_title = title
        self.image_name = name
        self.image_size = size
        self.is_selected = False
        self.is_nested = False
        self.parent = None

        iw_cover_box = Gtk.VBox()
        self.pack_start(iw_cover_box, False, True, 2)
        iw_songlist_scroll = ScrolledWindow()
        iw_songlist_scroll.set_policy(
            Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        iw_songlist_scroll.set_shadow_type(Gtk.ShadowType.NONE)

        iw_songlist_box = Gtk.VBox()
        iw_songlist_scroll.add(iw_songlist_box)
        iw_songlist_scroll.show_all()

        self.songlist = iw_songlist_box
        self.songlist.widgets = {}
        self.pack_start(iw_songlist_scroll, False, True, 0)

        iw_cover_box.pack_start(coverimage, True, True, 2)

        iw_hborder = Gtk.HBox()
        iw_hborder.pack_start(self, True, True, 3)
        iw_vborder = Gtk.VBox()
        iw_vborder.pack_start(iw_hborder, True, True, 3)

        iw_outer = Gtk.EventBox()
        iw_outer.add(iw_vborder)

        self.vborder = iw_vborder
        self.hborder = iw_hborder

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
        label_desc.set_no_show_all(True)
        label_desc.set_visible(True)
        label_desc.set_line_wrap(True)
        label_desc.set_use_markup(True)
        label_desc.set_tooltip_markup(image.path)
        self.label = label_desc
        iw_cover_box.pack_start(label_desc, False, True, 4)

        iw_outer_align = Align(bottom=15)
        iw_outer_align.add(iw_outer)
        self.outer = iw_outer_align
        self.outer.connect("button-press-event", self.highlight_toggle_cb)

        # access
        coverimage.box = self
        self.outer.image_widget = self

    def highlight_toggle_cb(self, event, *data):
        self.image_widget.highlight_toggle()

    def highlight_toggle(self, force_highlight=None):
        scv = self.vborder.get_style_context()
        sch = self.hborder.get_style_context()
        if force_highlight is False or \
            (scv.has_class('highlightbox') and
             force_highlight is not True):
            scv.remove_class('highlightbox')
            sch.remove_class('highlightbox')
            self.is_selected = False
        else:
            if not scv.has_class('highlightbox'):
                scv.add_class('highlightbox')
                sch.add_class('highlightbox')
            self.is_selected = True
        self.signalbox.emit('select-count-changed')
        self.signalbox.emit('subselect-count-changed')

    def selected(self):
        if CONFIG.collapsed_view:
            if self.is_nested:
                return self.parent.is_selected and \
                           self.parent.nested_active[self]
            else:
                return self.nested_active[self]
        else:
            return self.is_selected

    def collapsed(self, visible):
        if visible:
            # (re)build song list
            for w in self.songlist.get_children():
                self.songlist.remove(w)
            self.songlist.set_size_request(200, -1)
            for k, g in groupby(self.nested,
                                lambda w: w.song['album']):
                album_cb = self.__add_nested_album(self, k)

                active_all = True
                for iw in sorted(g, key=lambda iw2: iw2.song("~#track")):
                    cb = self._add_nested_image_widget(self, iw)
                    if active_all:
                        active_all = cb.get_active()

                album_cb.set_active(active_all)

            self.label.hide()
            self.songlist.get_parent().show_all()
        else:
            self.nested = [self]
            active = False
            if self in self.nested_active:
                active = self.nested_active[self]
            self.nested_active = {self: active}
            self.songlist.set_size_request(-1, -1)
            self.songlist.hide()
            self.label.show()

    def __cover_click_single(self, coverimage):
        if self.__double_clicked:
            return False
        coverimage.box.highlight_toggle()
        return False

    def __cover_click_double(self, coverimage):
        coverimage._show_cover()

    def __cover_click(self, coverimage, event):
        if event.type == Gdk.EventType.BUTTON_PRESS:
            self.__double_clicked = False
            GLib.timeout_add(DOUBLE_CLICK_TIMEOUT,
                             self.__cover_click_single, coverimage)
        elif event.type == Gdk.EventType._2BUTTON_PRESS:
            self.__double_clicked = True
            self.__cover_click_double(coverimage)
        return True

    def __nested_album_toggled(self, w, name, active):
        for iw, w in w.songlist.widgets.items():
            if iw.song['album'] == name:
                w.set_active(active)

    def __nested_song_toggled(self, w, w2, active):
        w.nested_active[w2] = active
        self.signalbox.emit('subselect-count-changed')

    def __get_nested_album(self, iw_root, name):
        for w in iw_root.songlist.get_children():
            if isinstance(w, Gtk.CheckButton) and w.album == name:
                return w
        return self.__add_nested_album(iw_root, name)

    def __add_nested_album(self, iw_root, name):

        cb = Gtk.CheckButton(name)
        cb.get_children()[0].get_style_context()\
            .add_class("boldandbig2")
        if gtk_version >= (3, 20):
            add_css(cb, """
                .checkbutton indicator {
                    min-height: 6px;
                    min-width: 6px;
                }""", True)
        else:
            add_css(cb, """
                GtkCheckButton {
                    -GtkCheckButton-indicator-size: 6;
                }""", True)
        cb.connect("toggled",
            lambda w, iw=iw_root, *_:
                self.__nested_album_toggled(iw, w.get_children()[0]
                    .get_text(), w.get_active()))

        iw_root.songlist.pack_start(cb, False, False, 2)
        entries_box = Gtk.VBox(spacing=1)
        entries_align = Align(left=10)
        entries_align.add(entries_box)
        iw_root.songlist.pack_start(entries_align, False, False, 2)

        cb.album = name
        cb.box = entries_box

        return cb

    def _add_nested_image_widget(self, iw_root, iw, entries_box=None):

        if not entries_box:
            entries_box = \
                self.__get_nested_album(iw_root, iw.song['album']).box

        iw.parent = iw_root
        if not iw in iw_root.nested_active:
            iw_root.nested.append(iw)
            iw_root.nested_active[iw] = False

        s = iw.song
        track = s('~#track')
        label = "%s%s" % (str(track) + ' | '
                          if track else "",
                          s['title'])
        cb = Gtk.CheckButton(label)
        cb.get_child().set_line_wrap(True)
        cb.set_tooltip_markup(s['~filename'])
        cb.connect("toggled",
            lambda w, iw_root=iw_root, iw=iw, *_:
                self.__nested_song_toggled(iw_root, iw, w.get_active()))
        active = False
        active = iw_root.nested_active[iw]
        cb.set_active(active)
        entries_box.pack_start(cb, False, False, 0)
        iw_root.songlist.widgets[iw] = cb
        if gtk_version >= (3, 20):
            add_css(cb, """
                .checkbutton indicator {
                    min-height: 10px;
                    min-width: 10px;
                }""", True)
        else:
            add_css(cb, """
                GtkCheckButton {
                    -GtkCheckButton-indicator-size: 10;
                }""", True)
        cb.set_visible(True)

        return cb

    def _remove_nested_image_widget(self, iw_root, iw):

        album = iw.song['album']
        album_cb = self.__get_nested_album(iw_root, album)
        box = album_cb.box
        iw_root.nested.remove(iw)
        del iw_root.nested_active[iw]
        box.remove(iw_root.songlist.widgets[iw])
        if not box.get_children():
            # remove album cb and container
            iw_root.songlist.remove(box.get_parent())  # align
            iw_root.songlist.remove(album_cb)


class EmbeddedArtBoxSignalBox(GObject.GObject):

    __gsignals__ = {
        "subselect-count-changed":
        (GObject.SignalFlags.RUN_LAST, None, [int]),
        "subtotal-count-changed":
        (GObject.SignalFlags.RUN_LAST, None, [int]),
        "select-count-changed":
        (GObject.SignalFlags.RUN_LAST, None, [int]),
        "total-count-changed":
        (GObject.SignalFlags.RUN_LAST, None, [int])
    }


class EmbeddedArtBox(Gtk.HBox):

    def __init__(self):
        super(EmbeddedArtBox, self).__init__()

        self.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)

        self.signalbox = EmbeddedArtBoxSignalBox()

        self.__songs = None

        self.__covers_select_count = 0
        self.__covers_total_count = 0

        # covers stack
        self.image_widgets = []
        self.covers_max = 50

    @property
    def subselect_count(self):
        return self.__covers_subselect_count

    @subselect_count.setter
    def subselect_count(self, value):
        self.__covers_subselect_count = value
        self.signalbox.emit('subselect-count-changed', value)

    @property
    def subtotal_count(self):
        return self.__covers_subtotal_count

    @subtotal_count.setter
    def subtotal_count(self, value):
        self.__covers_subtotal_count = value
        self.signalbox.emit('subtotal-count-changed', value)

    @property
    def select_count(self):
        return self.__covers_select_count

    @select_count.setter
    def select_count(self, value):
        self.__covers_select_count = value
        self.signalbox.emit('select-count-changed', value)

    @property
    def total_count(self):
        return self.__covers_total_count

    @total_count.setter
    def total_count(self, value):
        self.__covers_total_count = value
        self.signalbox.emit('total-count-changed', value)

    def get_visible_widgets(self):
        return [w for w in self.get_children()
                    if w.get_visible()]

    def get_selected_widgets(self):
        return [w for w in self.get_visible_widgets()
                    if w.image_widget.is_selected]

    def get_selected_image_widgets(self):
        return [iw for w in self.get_selected_widgets()
                       for iw in w.image_widget.nested
                           if iw.selected()]

    def update_subselect_count(self):
        self.subselect_count = \
            len(self.get_selected_image_widgets())

    def update_subtotal_count(self):
        self.subtotal_count = len(self.image_widgets)

    def update_select_count(self):
        self.select_count = sum(1 for w in self.get_selected_widgets())

    def update_total_count(self):
        self.total_count = sum(1 for w in self.get_visible_widgets())

    def update_counts(self):
        self.update_subselect_count()
        self.update_subtotal_count()
        self.update_select_count()
        self.update_total_count()

    def update(self, songs):

        self.__songs = songs

        self.__clear_covers()

        if not songs:
            return

        for song in songs:
            if len(self.image_widgets) == self.covers_max:
                print_d("covers max hit, ignoring then rest!")
                break
            widgets = self.__generate_covers(song)
            for w in widgets:
                self.pack_start(w, False, False, 2)
                self.image_widgets.append(w.image_widget)

        self.show_all()

        self.select_count = 0
        self.total_count = len(self.image_widgets)
        if CONFIG.collapsed_view:
            self._collapse_toggle(CONFIG.collapsed_view)

    def _refresh(self, songs):

        # the root iw's nested array will hold either a single iw
        # (itself), or a set of iws of which our song is one of.
        # in 'collapsed view' the outer container of the root iw is
        # either visible (nested set contains itself, and other 'nested'
        # iws which all have HIDDEN outer containers), or hidden (nested
        # set contains itself only)

        for s in songs:
            # old, ignore root/nested duplicates
            widgets_old = list(set(iw
                                   for outer in self.get_children()
                                       for iw in outer.image_widget.nested
                                           if iw.song == s))
            image_hashes_old = {}
            for iw in widgets_old:
                if not iw.image.data_hash in image_hashes_old:
                    image_hashes_old[iw.image.data_hash] = 1
                    continue
                image_hashes_old[iw.image.data_hash] += 1

            # new
            widgets_new = [w.image_widget for w in self.__generate_covers(s)]
            image_hashes_new = {}
            for iw in widgets_new:
                if not iw.image.data_hash in image_hashes_new:
                    image_hashes_new[iw.image.data_hash] = 1
                    continue
                image_hashes_new[iw.image.data_hash] += 1

            # 'widgets_old' will contain
            # # flat mode
            # -this song's set of iws, 1 iw per image
            # # collapsed mode
            # -this song's set of album art, one or more of which may me in
            #  use as the container for all iws using the image. however, if
            #  none of this song's iws have been used as roots, then there
            #  will also be additional (foreign song) iws in the set, whose
            #  art is the same as our song's

            # 'widgets_new' will contain
            # -a straight forward set of image widgets which represent the
            #  new state of the modified song. we ultimately just need to
            #  ensure that these and these alone are represented in the ui

            # drop missing
            # note: in collapsed view mode an iws nested set can contain
            # -multiple instances from the same song (duplicate art!)
            # -multiple songs using the same art from the same or different
            #  albums
            for idx in xrange(len(widgets_old) - 1, -1, -1):
                iw = widgets_old[idx]
                outer = iw.outer
                iw_root = iw
                if iw.parent:
                    iw_root = iw.parent
                    outer = iw_root.outer
                if not outer.get_visible():
                    continue
                if not iw.selected():
                    continue
                nested = iw.is_nested and outer.get_visible()

                hash_ = iw.image.data_hash
                if not hash_ in image_hashes_new or \
                    image_hashes_old[hash_] > image_hashes_new[hash_]:
                    # remove selected
                    if nested:
                        for idx2 in xrange(len(iw_root.nested) - 1, -1, -1):
                            if iw_root.nested[idx2] == iw:
                                iw_root.\
                                    _remove_nested_image_widget(iw_root, iw)
                                # remove its (hidden) outer
                                self.remove(iw.outer)
                                self.image_widgets.remove(iw)
                                image_hashes_old[hash_] -= 1
                                del widgets_old[idx]
                                break

                        if iw_root.song != s:
                            # nested entry removed from foreign song container
                            continue

                    else:
                        self.remove(outer)
                        self.image_widgets.remove(iw)
                        image_hashes_old[hash_] -= 1
                        del widgets_old[idx]
                        iw.nested.remove(iw)
                        del iw.nested_active[iw]

                        if iw.nested:
                            # container not empty, but all references to this
                            # image from the current song have gone so we need
                            # to remove this outer by first shifting the
                            # remaining nested set to the next valid entry's
                            # container
                            idx = 0
                            iw_next = iw.nested[idx]
                            while iw_next.song == s:
                                idx += 1
                                iw_next = iw.nested[idx]
                            iw_next.nested = iw.nested
                            iw_next.nested_active = iw.nested_active
                            for w in iw_next.nested:
                                w.parent = iw_next
                            iw_next.collapsed(CONFIG.collapsed_view)
                            # set selected
                            iw_next.highlight_toggle(force_highlight=False)
                            # ensure visible
                            iw_next.outer.set_visible(True)

            # insert new
            # where do we add the new image?
            # it we're in collapsed view, we have to first try and find
            # other instances of it, only one of which will be visible
            # (any others should be nested within this)
            # if it doesn't exist, then add next to any existing images
            # for the song
            idx = 0
            for w in self.get_children():
                if w.image_widget.song == s:
                    idx = self.child_get_property(w, 'position')
                    break

            image_hashes_global = {}
            for iw in widgets_new:
                hash_ = iw.image.data_hash
                if not hash_ in image_hashes_old or \
                   image_hashes_old[hash_] < image_hashes_new[hash_]:
                    if CONFIG.collapsed_view:
                        if not image_hashes_global:
                            for outer in self.get_children():
                                if not outer.get_visible():
                                    continue
                                iw_root = outer.image_widget
                                image_hashes_global[
                                    iw_root.image.data_hash] = iw_root
                        iw.collapsed(True)
                        if iw.image.data_hash in image_hashes_global:
                            # add nested
                            iw.is_nested = True
                            iw_root = image_hashes_global[iw.image.data_hash]
                            iw_root._add_nested_image_widget(iw_root, iw)

                    # add
                    self.pack_start(iw.outer, False, False, 2)
                    self.reorder_child(iw.outer, idx)
                    self.image_widgets.append(iw)
                    iw.outer.show_all()
                    iw.outer.set_visible(not iw.is_nested)
                    if not hash_ in image_hashes_old:
                        image_hashes_old[hash_] = 0
                    image_hashes_old[hash_] += 1

        self.update_counts()

    def _collapse_toggle(self, collapsed):
        if not self.total_count:
            return

        data_hashes = {}
        if collapsed:
            # songs sharing the same image share a single container
            for w in self.get_children():
                iw = w.image_widget
                iw.is_nested = False
                iw.parent = None
                h = iw.image.data_hash
                if h in data_hashes:
                    iw.is_nested = True
                    iw.parent = data_hashes[h]
                    data_hashes[h].nested.append(iw)
                    data_hashes[h].nested_active[iw] = iw.is_selected
                    w.hide()
                else:
                    iw.nested = [iw]
                    iw.nested_active[iw] = iw.is_selected
                    data_hashes[h] = iw

            for key, iw in data_hashes.items():
                iw.highlight_toggle(False)
                iw.collapsed(True)

        else:
            # flat display, one image to one (non-distinct) song
            iw_active = \
                {iw2: w.image_widget.nested_active[iw2]
                     for w in self.get_visible_widgets()
                         for iw2 in w.image_widget.nested}
            for w in self.get_children():
                iw = w.image_widget
                iw.is_nested = False
                iw.nested = [iw]
                iw.nested_active[iw] = iw_active[iw]
                iw.highlight_toggle(iw_active[iw])
                iw.collapsed(False)
                w.show()

        self.update_counts()

    def __clear_covers(self):
        self.image_widgets = []
        for w in self.get_children():
            self.remove(w)

    def __generate_covers(self, song):

        imageitems = self.__get_artwork(song)

        if len(self.image_widgets) + len(imageitems) >= self.covers_max:
            if self.image_widgets < self.covers_max:
                imageitems = imageitems[:self.covers_max -
                                         len(self.image_widgets)]

        widgets = []
        for image in imageitems:
            iw = ImageWidget(image, song)
            iw.signalbox.connect('subselect-count-changed',
                                 lambda _: self.update_subselect_count())
            iw.signalbox.connect('subselect-count-changed',
                                 lambda _: self.update_select_count())
            widgets.append(iw.outer)

        return widgets

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
                # ignore some mime-types, links..
                mime_ignore = ['-->']
                for i in images:
                    if i.mime_type in mime_ignore:
                        continue
                    data_hash = hashlib.md5(i.read()).hexdigest()
                    f = os.path.join(path_thumbs, data_hash)
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
                    imageitems.append(
                        ImageItem(f, pathfile, artist, album,
                                  width, height, False, data_hash))

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
            box = widget.image_widget
            box.label.set_text("%s%s%s" % (
                box.image_title,
                " [" + box.image_name + "]"
                if CONFIG.name_in_label else "",
                " [" + box.image_size + "]"
                if CONFIG.size_in_label else ""))
            box.label.set_use_markup(True)

    def update_tooltips(self):
        for widget in self.get_children():
            box = widget.image_widget
            box.cover.set_tooltip_markup(None)
            tooltip = []
            if CONFIG.size_in_tooltip:
                tooltip.append(box.image_size)
            if tooltip:
                box.cover.set_tooltip_markup('\n'.join(tooltip))

    def _clear_images(self):

        songs = [iw.song for iw in self.get_selected_image_widgets()]
        if not songs:
            songs = self.__songs
        if not songs:
            return

        for s in songs:
            if not s.can_change_images:
                ext = os.path.splitext(s['~filename'])[1][1:]
                print_d("skipping unsupported song type %r [%s]"
                        % (ext, s['~filename']))
                continue
            try:
                s.clear_images()
                self._refresh([s])
            except AudioFileError:
                print_exc()

    def _remove_image(self):

        for iw in self.get_selected_image_widgets():
            s = iw.song
            if not s.can_change_images:
                ext = os.path.splitext(s['~filename'])[1][1:]
                print_d("skipping unsupported song type %r [%s]"
                        % (ext, s['~filename']))
                continue

            images = s.get_images()
            if len(images) == 1:
                try:
                    s.clear_images()
                    self._refresh([s])
                except AudioFileError:
                    print_exc()
            else:
                # iterate and compare to find this image
                fo = open(iw.image.name, 'rb')
                for image in images:
                    if not self.__file_equals_embeddedimage(fo, image):
                        continue
                    try:
                        if s.remove_image(image):
                            self._refresh([s])
                        else:
                            print_d("failed to remove image for song %r"
                                    % s)
                    except AudioFileError:
                        print_exc()
                    break

    def _set_image(self):

        songs = [iw.song for iw in self.get_selected_image_widgets()]
        if not songs:
            songs = self.__songs
        if not songs:
            return

        for s in songs:
            if not s.can_change_images:
                ext = os.path.splitext(s['~filename'])[1][1:]
                print_d("skipping unsupported song type %r [%s]"
                        % (ext, s['~filename']))
                continue
            fh = app.cover_manager.get_cover(s)
            if not fh:
                print_d("no cover image available for song %r"
                        % (s['~filename']))
                continue
            pathfile = fh.name
            image = EmbeddedImage.from_path(pathfile)
            if not image:
                print_d("error creating embedded image %r for song %r"
                        % (pathfile, s['~filename']))
                continue
            try:
                s.set_image(image)
                self._refresh([s])
            except AudioFileError:
                print_exc()

    def _add_image(self):

        songs = [iw.song for iw in self.get_selected_image_widgets()]
        if not songs:
            songs = self.__songs
        if not songs:
            return

        pathfiles = self._choose_art_files()
        if not pathfiles:
            return

        images = []
        for pathfile in pathfiles:
            image = EmbeddedImage.from_path(pathfile)
            if not image:
                print_d("error creating embedded image %r" % pathfile)
                continue
            images.append(image)

        for s in songs:
            if not s.can_change_images:
                ext = os.path.splitext(s['~filename'])[1][1:]
                print_d("skipping unsupported song type %r [%s]"
                        % (ext, s['~filename']))
                continue

            for image in images:
                try:
                    s.add_image(image, strict=False)
                except AudioFileError:
                    print_exc()

            self._refresh([s])

    def _choose_art_files(self):
        image_types = ['jpg', 'png']
        patterns = ["*" + type_ for type_ in image_types]
        choose_filter = create_chooser_filter(_("Art Files"), patterns)
        return choose_files(self, _("Add Embedded Art"),
                            _("_Add File"), choose_filter)


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
            .boldandbig1 {
                font-weight: bold;
                font-size: 1.5em
            }
            .boldandbig2 {
                font-weight: bold;
                font-size: 1.2em
            }
            .warning {
                color: red;
                font-weight: bold;
            }
        """, True)

    def enabled(self):
        pass

    def disabled(self):
        # save data
        self.__save()

    def create_widgetbar(self):
        self.__widgetbar = WidgetBar(plugin_id)
        self.__controls = self.__widgetbar.box_left
        self.__content = self.__widgetbar.box
        self.__widgetbar.title.set_text(self.PLUGIN_NAME)

        self.__controls_box_outer = Gtk.VBox()
        self.__controls.pack_start(self.__controls_box_outer, False, False, 10)

        label_count_box = Gtk.HBox(spacing=2)
        label_count_box.set_size_request(175, 20)
        self.__controls_box_outer.pack_start(label_count_box, False, False, 10)
        label_count_prefix = Gtk.Label(_(u"Selection"))
        label_count_box.pack_start(label_count_prefix, False, False, 0)

        self.labels_subselect_box = Gtk.HBox()
        label_count_box.pack_start(self.labels_subselect_box, False, False, 0)
        self.labels_subselect_box.pack_start(Gtk.VSeparator(), False, False, 2)
        self.label_count_subselect = Gtk.Label()
        self.label_count_subselect.get_style_context().add_class("boldandbig1")
        self.label_count_subtotal = Gtk.Label()
        self.labels_subselect_box.pack_start(
            self.label_count_subselect, False, False, 0)
        self.labels_subselect_box.pack_start(Gtk.Label("of"), False, False, 2)
        self.label_count_subtotal.get_style_context().add_class("boldandbig2")
        self.labels_subselect_box.pack_start(
            self.label_count_subtotal, False, False, 0)
        self.labels_subselect_box.show_all()
        self.labels_subselect_box.set_no_show_all(True)
        self.labels_subselect_box.set_visible(False)
        self.labels_subselect_box.set_tooltip_text(
            _(u"selected songs count/total under selected embedded art items"
               "\n[collapsed view only]"))

        self.labels_select_box = Gtk.HBox()
        label_count_box.pack_start(self.labels_select_box, False, False, 0)
        self.labels_select_box.pack_start(Gtk.VSeparator(), False, False, 2)
        self.label_count_select = Gtk.Label()
        self.label_count_select.get_style_context().add_class("boldandbig1")
        self.labels_select_box.pack_start(
            self.label_count_select, False, False, 0)
        self.labels_select_box.pack_start(Gtk.Label("of"), False, False, 2)
        self.label_count_total = Gtk.Label()
        self.label_count_total.get_style_context().add_class("boldandbig2")
        self.labels_select_box.pack_start(
            self.label_count_total, False, False, 0)
        self.labels_select_box.show_all()
        self.labels_select_box.set_no_show_all(True)
        self.labels_select_box.set_visible(False)
        self.labels_select_box.set_tooltip_text(
            _(u"selected embedded art items count/total"))

        self.labels_noselect_box = Gtk.HBox()
        label_count_box.pack_start(self.labels_noselect_box, False, False, 0)
        self.labels_noselect_box.pack_start(Gtk.VSeparator(), False, False, 2)
        self.label_count_noselect = Gtk.Label()
        self.label_count_noselect.get_style_context().add_class("boldandbig1")
        self.labels_noselect_box.pack_start(
            self.label_count_noselect, False, False, 0)
        self.labels_noselect_box.show_all()
        self.labels_noselect_box.set_no_show_all(True)
        self.labels_noselect_box.set_visible(False)
        self.labels_noselect_box.set_tooltip_text(
            _(u"selected browser songs count"))

        align_covers = Gtk.Alignment(xalign=0.5, xscale=1.0)
        self.__embeddedart_box = EmbeddedArtBox()
        align_covers.add(self.__embeddedart_box)
        self.__content.pack_start(align_covers, True, True, 0)
        self.__content.show_all()

        self.__embeddedart_box.signalbox.connect(
            'subselect-count-changed',
            self.__embeddedart_on_subselect_count_changed)
        self.__embeddedart_box.signalbox.connect(
            'subtotal-count-changed',
            self.__embeddedart_on_subtotal_count_changed)
        self.__embeddedart_box.signalbox.connect(
            'select-count-changed',
            self.__embeddedart_on_select_count_changed)
        self.__embeddedart_box.signalbox.connect(
            'total-count-changed',
            self.__embeddedart_on_total_count_changed)

        self.__controls_box = Gtk.VBox()
        controls_align = Align(left=15, right=25)
        controls_align.add(self.__controls_box)
        self.__controls_scroll = ScrolledWindow()
        self.__controls_scroll.set_policy(
                Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.__controls_scroll.set_shadow_type(Gtk.ShadowType.NONE)
        self.__controls_scroll.add(controls_align)
        self.__controls_box_outer.pack_start(
            self.__controls_scroll, True, True, 0)

        single_box = Gtk.VBox(spacing=2)
        self.__controls_box.pack_start(single_box, False, False, 5)
        label_single_warning = Gtk.Label(_(u"WARNING!"))
        label_single_warning.get_style_context().add_class("warning")
        label_single_warning.set_tooltip_text(
            _(u"WARNING: this will delete all existing embedded images"))
        single_box.pack_start(label_single_warning, False, False, 0)
        self.__single_button = Gtk.Button(_(u"Single"))
        self.__single_button.connect(
            "button-press-event",
            lambda *_: self.__embeddedart_box._set_image())
        single_box.pack_start(self.__single_button, False, False, 0)

        self.__clear_button = Gtk.Button(_(u"Clear"))
        self.__clear_button.connect(
            "button-press-event",
            lambda *_: self.__embeddedart_box._clear_images())
        self.__controls_box.pack_start(self.__clear_button, False, False, 5)

        self.__remove_button = Gtk.Button(_(u"Remove"))
        self.__remove_button.connect(
            "button-press-event",
            lambda *_: self.__embeddedart_box._remove_image())
        self.__controls_box.pack_start(self.__remove_button, False, False, 5)

        self.__add_button = Gtk.Button(_(u"Add"))
        self.__add_button.connect(
            "button-press-event",
            lambda *_: self.__embeddedart_box._add_image())
        self.__controls_box.pack_start(self.__add_button, False, False, 5)

        collapse_ccb = ConfigCheckButton(_(u"Collapse"), "plugins",
                                         plugin_id + "_collapsed_view",
                                         populate=True)
        collapse_ccb.set_tooltip_text(_(u"collapse compatible images"))
        collapse_ccb.connect("toggled",
            lambda w, *_:
                self.__embeddedart_box._collapse_toggle(w.get_active()))
        self.__controls_box_outer.pack_end(collapse_ccb, False, False, 5)

        self.__subselect_count = 0
        self.__subtotal_count = 0
        self.__select_count = 0
        self.__total_count = 0
        self.__noselect_count = 0
        self.__update_count()

        self.live = True

        return self.__widgetbar

    def __update_count(self):
        self.label_count_subselect.set_text(str(self.__subselect_count))
        self.label_count_subtotal.set_text(str(self.__subtotal_count))
        self.label_count_select.set_text(str(self.__select_count))
        self.label_count_total.set_text(str(self.__total_count))
        self.label_count_noselect.set_text(str(self.__noselect_count))

        self.labels_subselect_box.set_visible(False)
        self.labels_select_box.set_visible(False)
        self.labels_noselect_box.set_visible(False)

        if self.__noselect_count:
            self.labels_noselect_box.set_visible(True)
            if self.__total_count:
                self.labels_select_box.set_visible(True)
                if CONFIG.collapsed_view:
                    self.labels_subselect_box.set_visible(True)

        sensitive = True if self.__select_count else False
        self.__remove_button.set_sensitive(sensitive)
        sensitive = True if self.__total_count else False
        self.__clear_button.set_sensitive(sensitive)
        sensitive = True if self.__noselect_count else False
        self.__single_button.set_sensitive(sensitive)
        self.__add_button.set_sensitive(sensitive)

    def __embeddedart_on_subselect_count_changed(self, object, count):
        self.__subselect_count = count
        self.__update_count()

    def __embeddedart_on_subtotal_count_changed(self, object, count):
        self.__subtotal_count = count
        self.__update_count()

    def __embeddedart_on_select_count_changed(self, object, count):
        self.__select_count = count
        self.__update_count()

    def __embeddedart_on_total_count_changed(self, object, count):
        self.__total_count = count
        self.__update_count()

    def plugin_on_songs_selected(self, songs):
        self.__noselect_count = len(songs)
        if not self.live:
            return
        self.__embeddedart_box.update(songs)
        self.__update_count()

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
