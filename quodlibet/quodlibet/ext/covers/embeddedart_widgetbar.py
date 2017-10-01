# -*- coding: utf-8 -*-
# Copyright 2017 Pete Beardmore
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License version 2 as
# published by the Free Software Foundation
import os
import hashlib
from senf import path2fsn
from gi.repository import Gtk, Gdk, GLib, GObject

from quodlibet import _
from quodlibet import app
from quodlibet.plugins import PluginConfig, BoolConfProp
from quodlibet.plugins.events import EventPlugin
from quodlibet.plugins.gui import UserInterfacePlugin
from quodlibet.formats import EmbeddedImage
from quodlibet.qltk import Icons, add_global_css
from quodlibet.qltk.widgetbar import WidgetBar
from quodlibet.qltk.x import Align
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


CONFIG = Config()
DOUBLE_CLICK_TIMEOUT = 200


class SignalBox(GObject.GObject):

    __gsignals__ = {
        "select-count-changed":
        (GObject.SignalFlags.RUN_LAST, None, [int]),
        "total-count-changed":
        (GObject.SignalFlags.RUN_LAST, None, [int])
    }


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

        self.signalbox = SignalBox()

        self.__covers_select_count = 0
        self.__covers_total_count = 0

        # covers stack
        self.image_widgets = []
        self.covers_max = 50

    def update(self, songs):
        if not songs:
            return

        self.__clear_covers()
        for song in songs:
            imageitems = self.__get_artwork(song)
            if len(self.image_widgets) == self.covers_max:
                print_d("covers max hit, ignoring then rest!")
                break
            self.__generate_covers(imageitems, song)

        self.select_count = 0
        self.total_count = len(self.image_widgets)

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

    def get_selected_widgets(self):
        return [w for w in self.get_children()
                    if w.image_widget.is_selected]

    def get_selected_image_widgets(self):
        return [w.image_widget for w in self.get_selected_widgets()]

    def update_select_count(self):
        self.select_count = sum(1 for w in self.get_selected_widgets())

    def update_total_count(self):
        self.total_count = len(self.image_widgets)

    def update_counts(self):
        self.update_select_count()
        self.update_total_count()

    def __clear_covers(self):
        self.image_widgets = []
        for w in self.get_children():
            self.remove(w)

    def __cover_click_single(self, coverimage):
        if self.__double_clicked:
            return False
        box = coverimage.get_parent()
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

    def __generate_covers(self, imageitems, song):
        if len(self.image_widgets) + len(imageitems) >= self.covers_max:
            if self.image_widgets < self.covers_max:
                imageitems = imageitems[:self.covers_max -
                                         len(self.image_widgets)]

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

            image_widget = Gtk.VBox()
            image_widget.image = image
            image_widget.song = song
            image_widget.cover = coverimage
            image_widget.image_title = title
            image_widget.image_name = name
            image_widget.image_size = size
            image_widget.is_selected = False

            image_widget.pack_start(coverimage, True, True, 2)

            image_widget_hborder = Gtk.HBox()
            image_widget_hborder.pack_start(
                image_widget, True, True, 3)
            image_widget_vborder = Gtk.VBox()
            image_widget_vborder.pack_start(
                image_widget_hborder, True, True, 3)

            image_widget_outer = Gtk.EventBox()
            image_widget_outer.add(image_widget_vborder)

            def highlight_toggle(box):
                scv = box.vborder.get_style_context()
                sch = box.hborder.get_style_context()
                if scv.has_class('highlightbox'):
                    scv.remove_class('highlightbox')
                    sch.remove_class('highlightbox')
                    box.is_selected = False
                else:
                    scv.add_class('highlightbox')
                    sch.add_class('highlightbox')
                    box.is_selected = True
                self.update_select_count()

            image_widget.vborder = image_widget_vborder
            image_widget.hborder = image_widget_hborder
            image_widget.highlight_toggle = highlight_toggle

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
            image_widget.pack_start(label_desc, False, True, 4)

            image_widget_outer_align = Align(bottom=15)
            image_widget_outer_align.add(image_widget_outer)

            image_widget_outer_align.image_widget = image_widget
            image_widget.outer = image_widget_outer_align

            def highlight_toggle_cb(widget, event, *data):
                widget.image_widget.highlight_toggle(widget.image_widget)

            image_widget_outer_align.connect(
                "button-press-event", highlight_toggle_cb)

            self.pack_start(image_widget_outer_align, True, False, 5)

            self.image_widgets.append(image_widget)

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

    def _clear_images(self):

        for w in self.get_selected_image_widgets():
            if not w.song.can_change_images:
                ext = os.path.splitext(w.song['~filename'])[1][1:]
                print_d("skipping unsupported song type %r [%s]"
                        % (ext, w.song['~filename']))
                continue
            try:
                w.song.clear_images()
                self._remove_widget_by_image_widget(w)
            except AudioFileError:
                print_exc()

    def _remove_image(self):

        for w in self.get_selected_image_widgets():
            if not w.song.can_change_images:
                ext = os.path.splitext(w.song['~filename'])[1][1:]
                print_d("skipping unsupported song type %r [%s]"
                        % (ext, w.song['~filename']))
                continue

            images = w.song.get_images()
            if len(images) == 1:
                try:
                    w.song.clear_images()
                    self._remove_widget_by_image_widget(w)
                except AudioFileError:
                    print_exc()
            else:
                # iterate and compare to find this image

                fo = open(w.image.name, 'rb')
                for image in images:
                    if self.__file_equals_embeddedimage(fo, image):
                        try:
                            if not w.song.remove_image(image):
                                print_d("failed to remove image for song %r"
                                        % w.song)
                        except AudioFileError:
                            print_exc()
                        break

    def _set_image(self):

        for w in self.get_selected_image_widgets():
            if not w.song.can_change_images:
                ext = os.path.splitext(w.song['~filename'])[1][1:]
                print_d("skipping unsupported song type %r [%s]"
                        % (ext, w.song['~filename']))
                continue
            fh = app.cover_manager.get_cover(w.song)
            if not fh:
                print_d("no cover image available for song %r"
                        % (w.song['~filename']))
                continue
            pathfile = fh.name
            image = EmbeddedImage.from_path(pathfile)
            if not image:
                print_d("error creating embedded image %r for song %r"
                        % (pathfile, w.song['~filename']))
                continue
            try:
                w.song.set_image(image)
            except AudioFileError:
                print_exc()

    def _add_image(self):

        pathfiles = self._choose_art_files()

        images = []
        for pathfile in pathfiles:
            image = EmbeddedImage.from_path(pathfile)
            if not image:
                print_d("error creating embedded image %r" % pathfile)
                continue
            images.append(image)

        for w in self.get_selected_image_widgets():
            if not w.song.can_change_images:
                ext = os.path.splitext(w.song['~filename'])[1][1:]
                print_d("skipping unsupported song type %r [%s]"
                        % (ext, w.song['~filename']))
                continue

            for image in images:
                try:
                    w.song.add_image(image)
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

        self.label_count_box = Gtk.HBox(spacing=2)
        self.label_count_box.set_size_request(150, -1)
        self.__controls_box_outer.pack_start(
            self.label_count_box, False, False, 10)
        label_count_prefix = Gtk.Label(_(u"Selected") + ": ")
        self.label_count_box.pack_start(
            label_count_prefix, False, False, 0)
        self.label_count_select = Gtk.Label()
        self.label_count_select.get_style_context().add_class("boldandbig1")
        self.label_count_box.pack_start(
            self.label_count_select, False, False, 0)
        self.label_count_separator = Gtk.Label()
        self.label_count_box.pack_start(
            self.label_count_separator, False, False, 0)
        self.label_count_total = Gtk.Label()
        self.label_count_total.get_style_context().add_class("boldandbig2")
        self.label_count_box.pack_start(
            self.label_count_total, False, False, 0)

        align_covers = Gtk.Alignment(xalign=0.5, xscale=1.0)
        self.__embeddedart_box = EmbeddedArtBox()
        align_covers.add(self.__embeddedart_box)
        self.__content.pack_start(align_covers, True, True, 0)
        self.__content.show_all()

        self.__embeddedart_box.signalbox.connect(
            'select-count-changed',
            self.__embeddedart_on_select_count_changed)
        self.__embeddedart_box.signalbox.connect(
            'total-count-changed',
            self.__embeddedart_on_total_count_changed)

        self.__controls_box = Gtk.VBox()
        self.__controls_box_outer.pack_start(
            self.__controls_box, False, False, 0)

        single_box = Gtk.VBox(spacing=2)
        self.__controls_box.pack_start(single_box, False, False, 5)
        label_single_warning = Gtk.Label(_(u"WARNING!"))
        label_single_warning.get_style_context().add_class("warning")
        label_single_warning.set_tooltip_text(
            _(u"WARNING: this will delete all existing embedded images"))
        single_box.pack_start(label_single_warning, False, False, 0)
        single_button = Gtk.Button(_(u"Single"))
        single_button.connect(
            "button-press-event",
            lambda *_: self.__embeddedart_box._set_image())
        single_box.pack_start(single_button, False, False, 0)

        clear_button = Gtk.Button(_(u"Clear"))
        clear_button.connect(
            "button-press-event",
            lambda *_: self.__embeddedart_box._clear_images())
        self.__controls_box.pack_start(clear_button, False, False, 5)

        remove_button = Gtk.Button(_(u"Remove"))
        remove_button.connect(
            "button-press-event",
            lambda *_: self.__embeddedart_box._remove_image())
        self.__controls_box.pack_start(remove_button, False, False, 5)

        add_button = Gtk.Button(_(u"Add"))
        add_button.connect(
            "button-press-event",
            lambda *_: self.__embeddedart_box._add_image())
        self.__controls_box.pack_start(add_button, False, False, 5)

        self.__select_count = 0
        self.__total_count = 0
        self.__update_count()

        self.live = True

        return self.__widgetbar

    def __update_count(self):
        if self.__total_count:
            self.label_count_select.set_text(str(self.__select_count))
            self.label_count_total.set_text(str(self.__total_count))
            self.label_count_separator.set_text(" of ")
        else:
            self.label_count_select.set_text("")
            self.label_count_total.set_text("")
            self.label_count_separator.set_text("")

        map(lambda w, s=False if self.__select_count == 0
                              else True:
                w.set_sensitive(s),
            self.__controls_box.get_children())

    def __embeddedart_on_select_count_changed(self, ojbect, count):
        self.__select_count = count
        self.__update_count()

    def __embeddedart_on_total_count_changed(self, object, count):
        self.__total_count = count
        self.__update_count()

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
