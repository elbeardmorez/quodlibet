# -*- coding: utf-8 -*-
# Copyright 2017 Pete Beardmore
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.

import os
from gi.repository import Gtk

import quodlibet
from quodlibet import qltk
from quodlibet import _
from quodlibet.qltk.x import Align
from quodlibet.qltk.cbes import ComboBoxEntrySave
from quodlibet.qltk.window import PersistentWindowMixin


CLICK_ACTION_STORE = \
    os.path.join(quodlibet.get_user_dir(), "lists", "pluginclickactions")
CLICK_ACTION_DEFAULTS = \
"""
default | <pluginswidgetbar> | <plugin default action>
default | <pluginswidgetbar> | <plugin toggle>
default | <pluginswidgetbar> | <plugin preferences>
"""

DRAGDROP_ACTION_STORE = \
    os.path.join(quodlibet.get_user_dir(), "lists", "plugindragdropactions")
DRAGDROP_ACTION_DEFAULTS = \
"""
default | <pluginswidgetbar> | <plugin default action>
"""


class PluginActionSelector(qltk.Window, PersistentWindowMixin):

    def __init__(self, parent, plugin, click_cb, dragdrop_cb):
        super(PluginActionSelector, self).__init__(dialog=False)
        self.set_transient_for(qltk.get_top_parent(parent))
        self.set_title(plugin.name + " " + _(u"default actions"))
        self.set_default_size(450, -1)
        self.set_position(Gtk.WindowPosition.MOUSE)

        valid_items = ["default", plugin.id]

        box = Gtk.VBox()
        self.add(box)
        self.set_border_width(8)

        click_box = Gtk.HBox(spacing=6)
        click_label = Gtk.Label(_("clicks"))
        click_label.set_alignment(xalign=1, yalign=0.5)
        click_label_align = Align()
        click_label_align.set_size_request(45, 0)
        click_label_align.add(click_label)
        click_box.pack_start(click_label_align, False, True, 2)
        self.__click_cbes = \
            ComboBoxEntrySave(CLICK_ACTION_STORE,
                              CLICK_ACTION_DEFAULTS.split("\n"),
                              title=plugin.id + " " +
                                    _(u"default click actions"),
                              edit_title=_(u"Edit actions"),
                              validator=self.validate,
                              filter=self.__filter,
                              filter_data=valid_items)
        self.__click_cbes.get_child().set_text(click_cb)
        click_box.pack_end(self.__click_cbes, True, True, 2)
        box.pack_start(click_box, True, True, 5)

        dragdrop_box = Gtk.HBox(spacing=6)
        dragdrop_label = Gtk.Label(_("dragdrops"))
        dragdrop_label.set_alignment(xalign=1, yalign=0.5)
        dragdrop_label_align = Align()
        dragdrop_label_align.set_size_request(45, 0)
        dragdrop_label_align.add(dragdrop_label)
        dragdrop_box.pack_start(dragdrop_label_align, False, True, 2)
        self.__dragdrop_cbes = \
            ComboBoxEntrySave(DRAGDROP_ACTION_STORE,
                              DRAGDROP_ACTION_DEFAULTS.split("\n"),
                              title=plugin.id + " " +
                                    _("default dragdrop actions"),
                              edit_title=_(u"Edit actions"),
                              validator=self.validate,
                              filter=self.__filter,
                              filter_data=valid_items)
        self.__dragdrop_cbes.get_child().set_text(dragdrop_cb)
        dragdrop_box.pack_end(self.__dragdrop_cbes, True, True, 2)
        box.pack_start(dragdrop_box, True, True, 5)

        self.connect('destroy', self.__destroy)

        self.show_all()

        return None

    def run(self):
        Gtk.main()
        self.destroy()

    def __filter(self, model, iter, valid):
        value = model.get_value(iter, 0)
        return value is None or \
               model.get_value(iter, 2) or \
               any(v for v in valid if value.startswith(v))

    def validate(self, string):
        """return True/False for a given string"""
        if len(string) > 100:
            return True
        return False

    def __destroy(self, *data):
        Gtk.main_quit()

        self.click_cb = self.__click_cbes.get_child().get_text()
        self.dragdrop_cb = self.__dragdrop_cbes.get_child().get_text()
