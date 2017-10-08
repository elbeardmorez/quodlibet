# -*- coding: utf-8 -*-
# Copyright 2017 Pete Beardmore
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License version 2 as
# published by the Free Software Foundation
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


class SignalBox(GObject.GObject):

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


class EmbeddedArtBox(Gtk.HBox):

    def __init__(self):
        super(EmbeddedArtBox, self).__init__()

        self.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)

        self.signalbox = SignalBox()

        self.__songs = None

        self.__covers_select_count = 0
        self.__covers_total_count = 0

        # covers stack
        self.image_widgets = []
        self.covers_max = 50

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
                           if w.image_widget.nested_active[iw]]

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

    def __clear_covers(self):
        self.image_widgets = []
        for w in self.get_children():
            self.remove(w)

    def __cover_click_single(self, coverimage):
        if self.__double_clicked:
            return False
        box = coverimage.box

        box.highlight_toggle(box)
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

    def _collapse_toggle(self, collapsed):
        if not self.total_count:
            return

        data_hashes = {}
        if collapsed:
            # songs sharing the same image share a single container
            for w in self.get_children():
                iw = w.image_widget
                h = iw.image.data_hash
                if h in data_hashes:
                    data_hashes[h].nested.append(iw)
                    data_hashes[h].nested_active[iw] = iw.is_selected
                    w.hide()
                else:
                    iw.nested = [iw]
                    iw.nested_active[iw] = iw.is_selected
                    data_hashes[h] = iw

            for key, iw in data_hashes.items():
                iw.highlight_toggle(iw, False)
                iw.collapsed(iw, True)

        else:
            # flat display, one image to one (non-distinct) song
            iw_active = \
                {iw2: w.image_widget.nested_active[iw2]
                     for w in self.get_visible_widgets()
                         for iw2 in w.image_widget.nested}
            for w in self.get_children():
                iw = w.image_widget
                iw.nested = [iw]
                iw.collapsed(iw, False)
                if iw in iw_active:
                    iw.highlight_toggle(iw, iw_active[iw])
                w.show()

        self.update_counts()

    def __generate_covers(self, song):

        imageitems = self.__get_artwork(song)

        if len(self.image_widgets) + len(imageitems) >= self.covers_max:
            if self.image_widgets < self.covers_max:
                imageitems = imageitems[:self.covers_max -
                                         len(self.image_widgets)]

        widgets = []
        for image in imageitems:

            title = "<b>" + GLib.markup_escape_text(image.album) + "</b>"
            name = os.path.splitext(os.path.basename(image.name))[0] \
                       .split('_')[-1]
            size = "x".join([str(image.width), str(image.height)])

            coverimage = CoverImage(resize=True)
            coverimage.set_song(song)
            coverimage.cover_click_cb = self.__cover_click

            fsn = path2fsn(image.name)
            fo = open(fsn, "rb")
            coverimage.set_image(fo, name, image.external)

            image_widget = Gtk.HBox(spacing=0, homogeneous=False)
            image_widget_cover_box = Gtk.VBox()
            image_widget.pack_start(image_widget_cover_box, False, True, 2)
            image_widget_songlist_scroll = ScrolledWindow()
            image_widget_songlist_scroll.set_policy(
                Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
            image_widget_songlist_scroll.set_shadow_type(Gtk.ShadowType.NONE)

            image_widget_songlist_box = Gtk.VBox()
            image_widget_songlist_scroll.add(image_widget_songlist_box)
            image_widget_songlist_scroll.show_all()
            image_widget.songlist = image_widget_songlist_box
            image_widget.songlist.widgets = {}
            image_widget.pack_start(
                image_widget_songlist_scroll, False, True, 0)

            image_widget.image = image
            image_widget.song = song
            image_widget.nested = [image_widget]
            image_widget.nested_active = {image_widget: False}
            image_widget.cover = coverimage
            image_widget.image_title = title
            image_widget.image_name = name
            image_widget.image_size = size
            image_widget.is_selected = False

            image_widget_cover_box.pack_start(coverimage, True, True, 2)

            image_widget_hborder = Gtk.HBox()
            image_widget_hborder.pack_start(
                image_widget, True, True, 3)
            image_widget_vborder = Gtk.VBox()
            image_widget_vborder.pack_start(
                image_widget_hborder, True, True, 3)

            image_widget_outer = Gtk.EventBox()
            image_widget_outer.add(image_widget_vborder)

            def highlight_toggle(box, force_highlight=None):
                scv = box.vborder.get_style_context()
                sch = box.hborder.get_style_context()
                if force_highlight is False or \
                    (scv.has_class('highlightbox') and
                     force_highlight is not True):
                    scv.remove_class('highlightbox')
                    sch.remove_class('highlightbox')
                    box.is_selected = False
                else:
                    if not scv.has_class('highlightbox'):
                        scv.add_class('highlightbox')
                        sch.add_class('highlightbox')
                    box.is_selected = True
                self.update_select_count()
                self.update_subselect_count()

            image_widget.vborder = image_widget_vborder
            image_widget.hborder = image_widget_hborder
            image_widget.highlight_toggle = highlight_toggle

            def collapsed(widget, visible):
                if visible:
                    # (re)build song list
                    def album_toggled(w, name, active):
                        for iw, w in w.songlist.widgets.items():
                            if iw.song['album'] == name:
                                w.set_active(active)

                    def song_toggled(w, w2, active):
                        w.nested_active[w2] = active
                        self.update_subselect_count()

                    for w in widget.songlist.get_children():
                        widget.songlist.remove(w)
                    widget.songlist.set_size_request(200, -1)
                    widget.get_parent().check_resize()
                    for k, g in groupby(widget.nested,
                                        lambda w: w.song['album']):
                        album_cb = Gtk.CheckButton(k)
                        album_cb.get_children()[0].get_style_context()\
                            .add_class("boldandbig2")
                        if gtk_version >= (3, 20):
                            add_css(album_cb, """
                                .checkbutton indicator {
                                    min-height: 6px;
                                    min-width: 6px;
                                }""", True)
                        else:
                            add_css(album_cb, """
                                GtkCheckButton {
                                    -GtkCheckButton-indicator-size: 6;
                                }""", True)
                        album_cb.connect("toggled",
                            lambda w, iw=widget, *_:
                                album_toggled(iw, w.get_children()[0]
                                              .get_text(), w.get_active()))

                        widget.songlist.pack_start(album_cb,
                                                   False, False, 2)
                        active_all = True
                        for iw2 in sorted(g, key=lambda iw:
                                                        iw.song("~#track")):
                            s = iw2.song
                            track = s('~#track')
                            label = "%s%s" % (str(track) + ' | '
                                              if track else "",
                                              s['title'])
                            cb = Gtk.CheckButton(label)
                            cb_align = Align(left=10)
                            cb_align.add(cb)
                            cb.get_child().set_line_wrap(True)
                            cb.set_tooltip_markup(s['~filename'])
                            cb.connect("toggled",
                                lambda w, iw=widget, iw2=iw2, *_:
                                    song_toggled(iw, iw2, w.get_active()))
                            active = widget.nested_active[iw2]
                            cb.set_active(active)
                            if active_all:
                                active_all = active
                            widget.songlist.pack_start(cb_align,
                                                       False, False, 0)
                            widget.songlist.widgets[iw2] = cb
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
                        album_cb.set_active(active_all)

                    widget.label.hide()
                    widget.songlist.get_parent().show_all()
                else:
                    widget.nested = [widget]
                    widget.nested_active = {widget: True}
                    widget.songlist.set_size_request(-1, -1)
                    widget.songlist.hide()
                    widget.label.show()

            image_widget.collapsed = collapsed

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
            image_widget.label = label_desc
            image_widget_cover_box.pack_start(label_desc, False, True, 4)

            image_widget_outer_align = Align(bottom=15)
            image_widget_outer_align.add(image_widget_outer)

            coverimage.box = image_widget
            image_widget_outer_align.image_widget = image_widget
            image_widget.outer = image_widget_outer_align

            def highlight_toggle_cb(widget, event, *data):
                widget.image_widget.highlight_toggle(widget.image_widget)

            image_widget_outer_align.connect(
                "button-press-event", highlight_toggle_cb)

            widgets.append(image_widget_outer_align)

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
                    data_hash = hashlib.md5(i.read()).hexdigest()
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

    def _remove_widget_by_image_widget(self, image_widget):
        self.remove(image_widget.outer)
        self.image_widgets.remove(image_widget)
        self.update_select_count()
        self.total_count = len(self.image_widgets)

    def _remove_widget_by_song(self, song):
        for outer in self.get_children():
            w = outer.image_widget
            if len(w.nested) == 1:
                if w.nested[0].song == song:
                    self._remove_widget_by_image_widget(w)
            else:
                for idx in xrange(len(w.nested) - 1, -1, -1):
                    if w.nested[idx].song == song:
                        del w.nested[idx]
                        del w.nested_active[w]
                        w.songlist.remove(w.songlist.widgets[w])
                        del w.songlist.widgets[w]

    def _remove_widget_by_image(self, image):
        for outer in self.get_children():
            w = outer.image_widget
            if not w.image == image:
                continue

            if len(w.nested) == 1:
                self._remove_widget_by_image_widget(w)
            else:
                for idx in xrange(len(w.nested) - 1, -1, -1):
                    if w.nesteds[idx].image == image:
                        del w.nested[idx]
                        del w.nested_active[w]
                        w.songlist.remove(w.songlist.widgets[w])
                        del w.songlist.widgets[w]

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
                self._remove_widget_by_song(s)
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
                    self._remove_widget_by_song(s)
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
                            self._remove_widget_by_image(image)
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
