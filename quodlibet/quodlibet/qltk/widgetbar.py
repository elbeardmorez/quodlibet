# -*- coding: utf-8 -*-
# Copyright 2017 Pete Beardmore
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.

import os
from quodlibet.qltk.pluginwin import PluginWindow
from quodlibet.plugins import PluginManager

from quodlibet import config
from quodlibet import _
from quodlibet.util import print_d

from quodlibet.qltk import get_top_parent
from quodlibet.qltk import Icons
from quodlibet.qltk.menubutton import SmallMenuButton
from quodlibet.qltk.x import ScrolledWindow, SymbolicIconImage, \
    SmallImageButton, MenuItem, PaneLock, ExpanderTitleContainerHack

from gi.repository import Gtk, GLib

SCROLL_AREA = 60


class WidgetBar(Gtk.Expander):
    """A horizontally scrollable collapsible container intended for use by
    feature rich plugins requiring horizontal segments of space in the main UI.

    This container comprises 'box_left|box|box_right' accessible widget
    sections, with the center 'box' widget scrollable. It also features with a
    close button, and a preferences menu intended to be extended/overridden.

    These widgets are compatible with the main UI's 'dynamic elements'
    container, a MultiXVPaned. Developers should implement their
    UserInterfacePlugins overriding 'create_widgetbar' to return a (populated!)
    WidgetBar instance. Control of its position in the UI is currently only
    configurable via the 'WidgetBars' plugin dialog (or the
    '[plugins] widgetbars_pane_order' .config option

    TODO: generalise to allow better re-orientation of sections.
    """

    def __init__(self, id):
        super(WidgetBar, self).__init__(spacing=1)

        self.id = id
        self.panelock = PaneLock(self.id, Gtk.Orientation.VERTICAL, 100)
        self.panelock.size = config.getint("plugins",
                                 self.id + "_size", self.default_size)

        self.title = Gtk.Label()
        self.title.set_alignment(0.0, 0.5)
        self.set_label_widget(ExpanderTitleContainerHack(self.title))

        self.scroll = ScrolledWindow()
        self.scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)
        self.scroll.set_shadow_type(Gtk.ShadowType.NONE)

        self.preferences_cb = self.__preferences

        outer = Gtk.HBox()
        outer.props.vexpand = True
        self.box_left = Gtk.HBox()
        self.box_right = Gtk.HBox()
        self.box = Gtk.HBox()
        controls_box = Gtk.VBox()

        outer.pack_start(self.box_left, False, False, 0)
        self.scroll.add(self.box)
        outer.pack_start(self.scroll, True, True, 0)
        outer.pack_start(self.box_right, False, False, 0)
        outer.pack_end(controls_box, False, False, 0)

        self.menu = Gtk.Menu()
        menuitem_prefs = MenuItem(_("_Preferences"), Icons.EDIT_CLEAR)
        self.menu.append(menuitem_prefs)
        menuitem_prefs.connect("activate", self.preferences)

        button_menu = SmallMenuButton(
            SymbolicIconImage(Icons.EMBLEM_SYSTEM, Gtk.IconSize.MENU),
            arrow=True)
        button_menu.set_relief(Gtk.ReliefStyle.NONE)
        button_menu.show_all()
        button_menu.set_no_show_all(True)
        self.menu.show_all()
        button_menu.set_menu(self.menu)

        aligntop = Gtk.Alignment()
        aligntop.set(1.0, 0.5, 0, 0)
        aligntop.add(button_menu)
        controls_box.pack_start(aligntop, False, True, 0)

        button_close = SmallImageButton(
            image=SymbolicIconImage("window-close", Gtk.IconSize.MENU),
            relief=Gtk.ReliefStyle.NONE)
        button_close.connect("clicked", lambda *x: self.__disable())
        controls_box.pack_end(button_close, False, True, 0)

        self.title.set_text(self.id)
        self.add(outer)

        self.connect('notify::expanded', self.__expand, button_menu)
        self.connect('size_allocate', self.__size_allocate)
        self.connect("destroy", self.__destroy)

        expanded = config.getboolean("plugins", self.id + "_expanded", True)
        self.set_expanded(expanded)

    @property
    def default_size(self):
        return self.panelock.default_size

    @default_size.setter
    def default_size(self, value):
        self.panelock.default_size = value

    def preferences(self, data):
        if self.preferences_cb:
            self.preferences_cb()

    def __preferences(self):
        plugins_window = PluginWindow(get_top_parent(self))
        plugins_window.show()
        plugins_window.move_to(self.id)

    def __expand(self, widget, prop, menu_button):
        expanded = self.get_expanded()
        config.set("plugins", self.id + "_expanded", str(expanded))

        if self.get_parent():
            self.panelock.updating = True
            self.get_parent().update(self)
            self.get_parent().check_resize()
            self.panelock.updating = False

    def __disable(self):
        pm = PluginManager.instance
        plugin = next((p for p in pm.plugins if p.id == self.id), None)
        if plugin:
            pm.enable(plugin, False)
            pm.save()
        self.__save()

    def __scroll_size(self, offset):
        if offset > SCROLL_AREA:
            offset = SCROLL_AREA
        change = round((float(offset) / SCROLL_AREA)**1.5)
        if change <= 0:
            change = 1
#        print_d("scroll size offset %d, change %d" % (offset, change))
        return change

    def drag_scroll_setup(self):
        self.__drag_scroll = True
        self.__scrolling_size = 0

    def drag_scroll_disable(self):
        self.__drag_scroll = False
        self.__scroll_callback_handle = None
        self.__scrolling_size = 0

    def drag_scroll(self, mouse_pos):

        min_pos = SCROLL_AREA
        max_pos = self.scroll.get_allocation().width - SCROLL_AREA

        if not mouse_pos:
            return

        pos = mouse_pos[0]
        if pos < min_pos or pos > max_pos:
            self.__drag_scroll = True
            self.__scroll_callback_handle = \
                GLib.timeout_add(10, self.__scrolling)
        else:
            self.__drag_scroll = False

        if pos < min_pos:
            self.__scrolling_size = -1 * self.__scroll_size(min_pos - pos)
        elif pos > max_pos:
            self.__scrolling_size = self.__scroll_size(pos - max_pos)

    def __scrolling(self):

        if not self.__drag_scroll:
            return False

#        print_d("scrolling by %d" % self.__scrolling_size)
        adj = self.scroll.get_hadjustment()
        adj.set_value(adj.get_value() + self.__scrolling_size)

        return True

    def __destroy(self, *args):
        # no guarantee that this will be called -> :(
        self.__save()

    @staticmethod
    def read_datafile(pathfile, item_lines):
        items = []
        try:
            if os.path.exists(pathfile):
                fileobj = open(pathfile, "rU")
                lines = list(fileobj.readlines())
                for i in range(len(lines) // item_lines):
                    item = []
                    for ii in range(item_lines):
                        item.append(lines[i * item_lines + ii].strip())
                    items.append(item)
        except:
            print_d("error reading datafile %r" % pathfile)

        return items

    @staticmethod
    def write_datafile(pathfile, iterable, cb):
        try:
            if not os.path.isdir(os.path.dirname(pathfile)):
                os.makedirs(os.path.dirname(pathfile))
            with open(pathfile, "w") as saved:
                for i in iterable:
                    arr = cb(i)
                    for ii in arr:
                        saved.write(ii + "\n")
        except:
            print_d("error writing datafile %r" % pathfile)

    def __save(self):
        config.set("plugins", self.id + "_size", self.panelock.size)

    def __size_allocate(self, widget, allocation):
        self.panelock.size_allocate(allocation)
        # persistence overkill as __destroy failing
        config.set("plugins", self.id + "_size", self.panelock.size)
