# -*- coding: utf-8 -*-
# Copyright 2017 Pete Beardmore
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License version 2 as
# published by the Free Software Foundation

import os
from collections import OrderedDict
from inspect import isclass, getargspec
from gi.repository import Gtk

import quodlibet
from quodlibet import app
from quodlibet.qltk import get_top_parent
from quodlibet.util import print_d
from quodlibet import _
from quodlibet.plugins import PluginConfig, BoolConfProp, ConfProp
from quodlibet.plugins.events import EventPlugin
from quodlibet.plugins.gui import UserInterfacePlugin
from quodlibet.qltk.pluginwin import PluginWindow, PluginErrorWindow
from quodlibet.plugins import PluginManager
from quodlibet.qltk import Icons
from quodlibet.qltk.entry import UndoEntry
from quodlibet.qltk.x import Align
from quodlibet.qltk.widgetbar import WidgetBar
from quodlibet.plugins.actions import PluginActionSelector
from quodlibet.qltk.ccb import ConfigCheckButton


plugin_id = "pluginswidgetbar"


CLICK_ACTION_SET = \
    os.path.join(quodlibet.get_user_dir(),
                 "lists", "pluginclickactions.default")
DRAGDROP_ACTION_SET = \
    os.path.join(quodlibet.get_user_dir(),
                 "lists", "plugindragdropactions.default")


class Config(object):
    _config = PluginConfig(plugin_id)

    expanded = BoolConfProp(_config, "expanded", True)
    filter_positive = ConfProp(_config, "filter_positive", "")
    filter_negative = ConfProp(_config, "filter_negative", "")
    sort_order = ConfProp(_config, "sort_order", "")
    enabled_only = BoolConfProp(_config, "enabled_only", True)
    show_labels = BoolConfProp(_config, "show_labels", True)
    small_icons = BoolConfProp(_config, "small_icons", False)
    enable_errors_icon = BoolConfProp(_config, "enable_errors_icon", True)


CONFIG = Config()


class PluginsWidgetBarPlugin(UserInterfacePlugin, EventPlugin):
    """The plugin class."""

    PLUGIN_ID = "pluginswidgetbar"
    PLUGIN_NAME = _("Plugins Widget Bar")
    PLUGIN_DESC = _("Display the enabled plugins in a widget bar.")
    PLUGIN_CONFIG_SECTION = __name__
    PLUGIN_ICON = Icons.PREFERENCES_PLUGIN

    def __init__(self):
        super(PluginsWidgetBarPlugin, self).__init__()
        self.live = False
        self.__target_elements = {}
        self.__song_playing = None
        self.__songs_selected = None

    def enabled(self):
        # setup
        pass

    def disabled(self):
        # save data
        self.__save()

    def create_widgetbar(self):
        self.__widgetbar = WidgetBar(plugin_id)
        self.__widgetbar.default_size = 75
        self.__content = self.__widgetbar.box
        self.__content_left = self.__widgetbar.box_left
        self.__content_right = self.__widgetbar.box_right
        # override preferences menu callback
        self.__widgetbar.preferences_cb = self.__preferences
        # override title
        self.__widgetbar.title.set_text(self.PLUGIN_NAME)
        # populate widgetbar content
        label_box_align = Gtk.Alignment(yalign=0.5, yscale=0.0)
        label_box = Gtk.VBox()
        label_box_align.add(label_box)
        self.__content_left.pack_start(label_box_align, False, False, 5)
        self.__content_left.label = Gtk.Label("")
        self.__content_left.label.set_alignment(0.0, 0.5)
        label_box.pack_start(self.__content_left.label, False, False, 1)
        label = Gtk.Label("<b>Plugins:</b>")
        label.set_use_markup(True)
        label.set_alignment(0.0, 0.5)
        label_box.pack_start(label, False, False, 1)
        separator = Gtk.VSeparator()
        separator_align = Gtk.Alignment(yscale=0.8)
        separator_align.add(separator)
        self.__content_left.pack_start(separator_align, True, True, 2)

        self.__callbacks_click = {}
        self.__callbacks_dragdrop = {}
        self.__read_actions()

        self.live = True

        self.__update_plugins()

        return self.__widgetbar

    def plugin_on_song_started(self, song):
        self.__song_playing = song

    def plugin_on_song_ended(self, song, stopped):
        self.__song_playing = None

    def plugin_on_songs_selected(self, songs):
        self.__songs_selected = songs

    def plugin_on_plugin_toggled(self, plugin, enabled):
        # TODO: don't be so lazy
        self.__update_plugins()

    def __plugin_action_click(self, widget, event, plugin):

        if event.button == 3:
            self.__plugin_actions(plugin)
            return

        if event.button != 1:
            return

        # non-string arg lookup. add aliases here too
        args_set = {}
        args_set["library"] = args_set["librarian"] = app.library
        args_set["songs"] = self.__songs_selected \
                                if self.__songs_selected \
                                    else [self.__song_playing]
        args = []

        click_cb = ""
        if plugin.id in self.__callbacks_click:
            click_cb = \
                "|".join(self.__callbacks_click[plugin.id].split('|')[1:])
        click_target = []
        click_args = []
        click_target = \
            list(reversed(map(lambda s: s.strip('<> '),
                [s for ss in click_cb.split('|')
                                 [0:max(len(click_cb.split("|")) - 1, 2)]
                   for s in ss.split('.')])))
        if len(click_cb.split('|')) > 2:
            click_args = \
               list(reversed(map(lambda s: s.strip('<> '),
                          click_cb.split('|')[-1].split('.'))))
            if click_args:
                args = click_args

        if len(click_target) < 2:
            self.__plugin_actions_preferences(plugin)
            return

        if click_target[-1] == "pluginswidgetbar" and \
             click_target[-2] == "plugin default action":
            callback = self.__plugin_actions_preferences
            args = [plugin]
        elif click_target[-1] == "pluginswidgetbar" and \
             click_target[-2] == "plugin toggle":
            callback = self.__plugin_actions_toggle
            args = [plugin]
        elif click_target[-1] == "pluginswidgetbar" and \
             click_target[-2] == "plugin preferences":
            callback = self.__plugin_actions_preferences
            args = [plugin]
        else:
            # support for plugin or module based 'target'
            target_set = []
            try:
                pm = PluginManager.instance
                target = \
                    next((p for p in pm.plugins
                              if p.id == click_target[-1]), None)
                if target:
                    # look in plugin
                    target_set.append(target)
                    click_target.pop()
                    target = plugin.cls
                    target_set.append(target)
                else:
                    modules = {m.name: m for m in pm.modules}
                    if click_target[-1] in modules:
                        target = modules[click_target[-1]]
                        target_set.append(target)
                        click_target.pop()
                        elements = []
                        if target.name in self.__target_elements:
                            elements = \
                                self.__target_elements[target.name][1]
                        else:
                            elements = self.__target_expand(target.module)
                            self.__target_elements[target.name] = \
                                (target, elements)

                        if click_target[-1] in elements:
                            target = elements[click_target[-1]]
                            target_set.append(target)
                            click_target.pop()
                    else:
                        raise Exception()

                if not isinstance(target.__class__, target):
                    args_sub = self.__target_args(
                                   target.__init__, args, args_set)

                    # instantiate class
                    if args_sub:
                        target = target(*args_sub)
                    else:
                        target = target(args_sub)
                    target_set.append(target)

                if click_target:
                    if hasattr(target, click_target[-1]):
                        target = getattr(target, click_target[-1])
                        target_set.append(target)
                        click_target.pop()

                callback = target

            except Exception, e:
                print_d("error importing selected click target %r "
                        "for plugin %r:\n%s" % (click_cb, plugin.id, e))
                return

        # should hopefully just be calling a method on the target now
        try:
            args_sub = self.__target_args(callback, args, args_set)
            if args_sub:
                callback(*args_sub)
            else:
                callback(args_sub)
        except Exception, e:
            print_d("error calling selected click target %r "
                    "for plugin %r:\n%s" % (click_cb, plugin.id, e))
            return

    def __target_args(self, target, args, args_set):
        params = []
        try:
            params = getargspec(target).args
        except:
            if hasattr(target, 'get_arguments'):
                # max two args from tuple of Gtk arg infos
                params = map(lambda o: o.__name__, target.get_arguments())
            else:
                # hunt
                paths = [['__call__']] # add more paths to try
                params_set = False
                for p in paths:
                    target_pos = target
                    for pp in p:
                        if not hasattr(target_pos, pp):
                            break
                        else:
                            target_pos = getattr(target_pos, pp)
                    try:
                        params = getargspec(target_pos).args
                        params_set = True
                    except:
                        pass
                    if params_set:
                        break

        if params and params[0] == 'self':
            params = params[1:]

        args_sub = OrderedDict()
        for p in params:
            # try match by name
            if p in args_set:
                args_sub[p] = args_set[p]
                if args and args[-1] == p:
                    # pop if this was positional anyway
                    args.pop()
            else:
                # use passed positional
                if args:
                    args_sub[p] = args[-1]
                    args.pop()

        if len(params) > len(args_sub):
            raise Exception(
                      "more arguments needed for %r\n"
                      "args: %s, params: %s"
                      % (target, list(reversed(args_sub.keys())), params))
            return []
        else:
            return args_sub.values() # ordered

    def __target_expand(self, target):
        try:
            objs = [getattr(target, attr) for attr in target.__all__]
        except AttributeError:
            objs = [getattr(target, attr) for attr in vars(target)
                    if not attr.startswith("_")]

        classes = {obj.__name__: obj for obj in objs if isclass(obj)}
        return classes

    def __plugin_action_dragdrop(self, widget, event, plugin):
        if plugin.id in self.__callbacks_dragdrop:
            # TODO
            dragdrop_cb = self.__callbacks_dragdrop[plugin.id]
            try:
                dragdrop_cb()
            except:
                print_d("error calling selected dragdrop callback %r "
                        "for plugin %r" % (dragdrop_cb, plugin.id))

    def __plugin_actions_preferences(self, plugin):
        self.__preferences(plugin)

    def __plugin_actions_toggle(self, plugin):
        pm = PluginManager.instance
        if plugin:
            pm.enable(plugin, not pm.enabled(plugin))
            pm.save()

    def __preferences(self, plugin=None):
        window = PluginWindow(get_top_parent(self.__widgetbar))
        window.move_to(self.PLUGIN_ID if not plugin else plugin.id)
        window.show()

    def __save(self):
        print_d("saving config data")

    def __filter_positive_changed(self, widget, *data):
        print_d("__filter_positive_changed")
        CONFIG.filter_positive = widget.get_text()
        self.__update_plugins()
        return False

    def __filter_negative_changed(self, widget, *data):
        print_d("__filter_negative_changed")
        CONFIG.filter_negative = widget.get_text()
        self.__update_plugins()
        return False

    def __sort_order_changed(self, widget, *data):
        print_d("__sort_order_changed")
        CONFIG.sort_order = widget.get_text()
        self.__update_plugins()
        return False

    def __update_plugins(self):
        if not self.live:
            return

        self.__content_left.label.set_text(
            "<b>%s</b>"
            % (_(u'Enabled') if CONFIG.enabled_only else _(u'All')))
        self.__content_left.label.set_use_markup(True)
        self.__content_left.label.set_line_wrap(True)

        # sort and filter

        # condition maps
        sort_order = {}
        if CONFIG.sort_order:
            sort_order = OrderedDict.fromkeys(CONFIG.sort_order.split(','))
        filter_positive = {}
        if CONFIG.filter_positive:
            filter_positive = set(CONFIG.filter_positive.split(','))
        filter_negative = {}
        if CONFIG.filter_negative:
            filter_negative = set(CONFIG.filter_negative.split(','))
        enabled_only = CONFIG.enabled_only

        plugins_map = OrderedDict()
        plugins_map_first = OrderedDict()
        pm = PluginManager.instance

        # map plugins and discard disabled if required
        for p in pm.plugins:
            if enabled_only and not pm.enabled(p):
                continue
            if p.id in sort_order:
                plugins_map_first[p.id] = p
            else:
                plugins_map[p.id] = p

        # filter and combine sorted and extras
        plugins = []
        if filter_positive:
            for id in sort_order:
                if id in filter_positive and id in plugins_map_first:
                    plugins.append(plugins_map_first[id])
                    filter_positive.discard(id)
            plugins.extend([p for id, p in plugins_map.items()
                            if id in filter_positive])
        elif filter_negative:
            for id in sort_order:
                if id not in filter_negative:
                    if id in plugins_map_first:
                        plugins.append(plugins_map_first[id])
                else:
                    filter_negative.discard(id)
            plugins.extend([p for id, p in plugins_map.items()
                            if id not in filter_negative])
        else:
            for id in sort_order:
                if id in plugins_map_first:
                    plugins.append(plugins_map_first[id])
            plugins.extend(plugins_map.values())

        # clear container
        self.__content.foreach(lambda w: self.__content.remove(w))
        # (re)display icons
        icon_size = self.__icon_size
        for p in plugins:
            plugin_box = self.__icon_box(p.name, p.id, pm.enabled(p),
                                         p.icon, icon_size,
                                         self.__plugin_action_click, p,
                                         self.__plugin_action_dragdrop, p)
            self.__content.pack_start(plugin_box, False, False, 0)

        self.__content.show_all()

        self.__update_errors_icon()

    def __icon_box(self, name, id, enabled, icon, icon_size,
                   click_cb, click_cb_data, dragdrop_cb, dragdrop_cb_data,
                   show_tooltip=True, show_label=None):

        if not show_label:
            show_label = CONFIG.show_labels

        plugin_align = Align(bottom=15, top=2) # clear scroll
        plugin_box_outer = Gtk.VBox()
        plugin_box_events = Gtk.EventBox()
        plugin_box_events.set_above_child(True)
        plugin_box_events.add(plugin_box_outer)
        plugin_align.add(plugin_box_events)

        plugin_separator_box_align = Align(left=6, right=6)
        plugin_separator_box_align.set_no_show_all(True)
        plugin_separator_box_align.set_size_request(-1, 5)
        plugin_separator_box = Gtk.VBox()
        plugin_separator_box_align.add(plugin_separator_box)

        plugin_box_inner = Gtk.VBox()
        plugin_box_align = Align(left=4, right=4)
        plugin_box_align.add(plugin_box_inner)

        plugin_icon_image = Gtk.Image.new_from_icon_name(
            icon or Icons.SYSTEM_RUN, icon_size)
        if show_tooltip:
            plugin_icon_image.set_tooltip_markup(
                _("name") + (": %s\nid: %s" % (name, id)))
        padding = 10 if CONFIG.show_labels else 5
        plugin_icon_align = \
            Align(left=padding, right=padding, top=0, bottom=0)
        plugin_icon_align.add(plugin_icon_image)
        # click action
        plugin_box_events.connect('button-press-event',
                                  click_cb, click_cb_data)
        plugin_box_inner.pack_start(plugin_icon_align, True, True, 0)

        if show_label:
            plugin_label = Gtk.Label(name)
            plugin_label.set_line_wrap(True)
            plugin_label.set_use_markup(True)
            plugin_box_inner.pack_start(plugin_label, True, True, 2)

        plugin_box_outer.pack_start(plugin_box_align, True, False, 0)

        return plugin_align

    def __read_actions(self):
        items = self.__widgetbar.read_datafile(CLICK_ACTION_SET, 2)
        for kv in items:
            self.__callbacks_click[kv[0]] = kv[1]
        items = self.__widgetbar.read_datafile(DRAGDROP_ACTION_SET, 2)
        for kv in items:
            self.__callbacks_dragdrop[kv[0]] = kv[1]

    def __write_actions(self):
        self.__widgetbar.write_datafile(
            CLICK_ACTION_SET, iter(self.__callbacks_click.items()),
            lambda x: x)
        self.__widgetbar.write_datafile(
            DRAGDROP_ACTION_SET, iter(self.__callbacks_dragdrop.items()),
            lambda x: x)

    def __plugin_actions(self, plugin):

        plugin_id = plugin.id

        click_cb = ""
        if plugin_id in self.__callbacks_click:
            click_cb = self.__callbacks_click[plugin_id]
        dragdrop_cb = ""
        if plugin_id in self.__callbacks_dragdrop:
            dragdrop_cb = self.__callbacks_dragdrop[plugin_id]

        selector = PluginActionSelector(
                       self.__widgetbar, plugin, click_cb, dragdrop_cb)
        selector.run()

        # process selections
        persist = False

        click_cb_new = selector.click_cb
        if click_cb_new:
            if plugin_id in self.__callbacks_click:
                if self.__callbacks_click[plugin_id] != click_cb_new:
                    persist = True
                    self.__callbacks_click[plugin_id] = click_cb_new
            else:
                persist = True
                self.__callbacks_click[plugin_id] = click_cb_new
        else:
            if plugin_id in self.__callbacks_click:
                persist = True
                del self.__callbacks_click[plugin_id]

        dragdrop_cb_new = selector.dragdrop_cb
        if dragdrop_cb_new:
            if plugin_id in self.__callbacks_dragdrop:
                if self.__callbacks_dragdrop[plugin_id] != dragdrop_cb_new:
                    persist = True
                    self.__callbacks_dragdrop[plugin_id] = dragdrop_cb_new
            else:
                persist = True
                self.__callbacks_dragdrop[plugin_id] = dragdrop_cb_new
        else:
            if plugin_id in self.__callbacks_dragdrop:
                persist = True
                del self.__callbacks_dragdrop[plugin_id]

        if persist:
            self.__write_actions()

    def __show_errors(self):
        pm = PluginManager.instance
        window = PluginErrorWindow(
                     get_top_parent(self.__content_right), pm.failures)
        window.show()

    def __update_errors_icon(self):
        self.__content_right.foreach(
            lambda w: self.__content_right.remove(w))
        if CONFIG.enable_errors_icon:
            self.__content_right.pack_start(
                self.__icon_box(
                    "Show Errors", "", False, Icons.DIALOG_WARNING,
                    self.__icon_size, lambda *x: self.__show_errors(),
                    None, None, None),
                False, False, 5)
            self.__content_right.show_all()

    @property
    def __icon_size(self):
        return Gtk.IconSize.SMALL_TOOLBAR \
                   if CONFIG.small_icons \
                   else Gtk.IconSize.LARGE_TOOLBAR

    def PluginPreferences(self, window):

        box = Gtk.VBox(spacing=6)

        # filters
        filters = [
            (CONFIG.filter_positive,
             _("Include filter"),
             _("Overrides ignore filter"),
             self.__filter_positive_changed),
            (CONFIG.filter_negative,
             _("Ignore filter"),
             _("Overridden by include filter"),
             self.__filter_negative_changed),
            (CONFIG.sort_order,
             _("Sort order"),
             _("Pull your favourites to the front"),
             self.__sort_order_changed),
        ]
        for text, label, tooltip, changed_cb in filters:
            filter_box = Gtk.HBox(spacing=6)
            filter_entry = UndoEntry()
            filter_entry.set_text(text)
            filter_entry.connect('focus-out-event', changed_cb)
            filter_entry.set_tooltip_markup(tooltip)
            filter_label = Gtk.Label(label)
            filter_label.set_mnemonic_widget(filter_entry)
            filter_label.set_alignment(xalign=0, yalign=0.5)
            filter_label.set_size_request(60, -1)
            filter_box.pack_start(filter_label, False, True, 5)
            filter_box.pack_start(filter_entry, True, True, 0)
            box.pack_start(filter_box, True, True, 0)

        # toggles
        toggles = [
            (plugin_id + '_enabled_only', _("Show only _enabled plugins"),
             None, True, lambda w: self.__update_plugins(), 0),
            (plugin_id + '_show_labels', _("Show labels"),
             None, True, lambda w: self.__update_plugins(), 0),
            (plugin_id + '_small_icons', _("Use small icons"),
             None, True, lambda w: self.__update_plugins(), 0),
            (plugin_id + '_enable_errors_icon', _("Enable errors window icon"),
             None, True, lambda w: self.__update_errors_icon(), 0),
        ]
        for key, label, tooltip, default, changed_cb, indent in toggles:
            ccb = ConfigCheckButton(label, 'plugins', key,
                                    populate=True)
            ccb.connect("toggled", changed_cb)
            if tooltip:
                ccb.set_tooltip_text(tooltip)
            ccb_align = Align(left=indent)
            ccb_align.add(ccb)
            box.pack_start(ccb_align, True, True, 0)

        return box
