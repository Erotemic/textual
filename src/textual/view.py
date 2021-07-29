from __future__ import annotations

from itertools import chain
from typing import Callable, Iterable, ClassVar, TYPE_CHECKING

from rich.console import Console, ConsoleOptions, RenderResult, RenderableType
import rich.repr
from rich.style import Style

from . import events
from . import log
from .layout import Layout, NoWidget
from .geometry import Dimensions, Offset, Region
from .messages import UpdateMessage, LayoutMessage
from .reactive import Reactive, watch

from .widget import Widget, Widget


if TYPE_CHECKING:
    from .app import App


@rich.repr.auto
class View(Widget):

    layout_factory: ClassVar[Callable[[], Layout]]

    def __init__(self, layout: Layout = None, name: str | None = None) -> None:
        self.layout: Layout = layout or self.layout_factory()
        self.mouse_over: Widget | None = None
        self.focused: Widget | None = None
        self.size = Dimensions(0, 0)
        self.widgets: set[Widget] = set()
        self.named_widgets: dict[str, Widget] = {}
        self._mouse_style: Style = Style()
        self._mouse_widget: Widget | None = None
        super().__init__(name=name)

    def __init_subclass__(
        cls, layout: Callable[[], Layout] | None = None, **kwargs
    ) -> None:
        if layout is not None:
            cls.layout_factory = layout
        super().__init_subclass__(**kwargs)

    background: Reactive[str] = Reactive("")

    async def watch_background(self, value: str) -> None:
        self.layout.background = value

    scroll_x: Reactive[int] = Reactive(0)
    scroll_y: Reactive[int] = Reactive(0)

    @property
    def scroll(self) -> Offset:
        return Offset(self.scroll_x, self.scroll_y)

    virtual_size: Reactive[Dimensions] = Reactive(Dimensions(0, 0))

    # @property
    # def virtual_size(self) -> Dimensions:
    #     return self.layout.map.size if self.layout.map else Dimensions(0, 0)

    # virtual_width: Reactive[int | None] = Reactive(None)
    # virtual_height: Reactive[int | None] = Reactive(None)

    # @property
    # def virtual_size(self) -> Dimensions:
    #     virtual_width = self.virtual_width
    #     virtual_height = self.virtual_height
    #     return Dimensions(
    #         (virtual_width if virtual_width is not None else self.size.width),
    #         (virtual_height if virtual_height is not None else self.size.height),
    #     )

    # @virtual_size.setter
    # def virtual_size(self, size: tuple[int, int]) -> None:
    #     width, height = size
    #     self.virtual_width = width
    #     self.virtual_height = height

    # @property
    # def offset(self) -> Point:
    #     return Point(self.offset_x, self.offset_y)

    # @property
    # def viewport(self) -> Region:
    #     virtual_width = self.virtual_width
    #     virtual_height = self.virtual_height
    #     width = virtual_width if virtual_width is not None else self.size.width
    #     height = virtual_height if virtual_height is not None else self.size.height
    #     return Region(self.offset_x, self.offset_y, width, height)

    def __rich_console__(
        self, console: Console, options: ConsoleOptions
    ) -> RenderResult:
        return
        yield

    def __rich_repr__(self) -> rich.repr.RichReprResult:
        yield "name", self.name

    def __getitem__(self, widget_name: str) -> Widget:
        return self.named_widgets[widget_name]

    @property
    def is_visual(self) -> bool:
        return False

    @property
    def is_root_view(self) -> bool:
        return self.parent is self.app

    def is_mounted(self, widget: Widget) -> bool:
        return widget in self.widgets

    def render(self) -> RenderableType:
        return self.layout

    def get_offset(self, widget: Widget) -> Offset:
        return self.layout.get_offset(widget)

    def check_layout(self) -> bool:
        return super().check_layout() or self.layout.check_update()

    async def message_update(self, message: UpdateMessage) -> None:
        widget = message.widget
        assert isinstance(widget, Widget)
        display_update = self.root_view.layout.update_widget(self.console, widget)
        if display_update is not None:
            self.app.display(display_update)

    async def message_layout(self, message: LayoutMessage) -> None:
        await self.root_view.refresh_layout()

    async def mount(self, *anon_widgets: Widget, **widgets: Widget) -> None:

        name_widgets: Iterable[tuple[str | None, Widget]]
        name_widgets = chain(
            ((None, widget) for widget in anon_widgets), widgets.items()
        )
        for name, widget in name_widgets:
            name = name or widget.name
            if self.app.register(widget, self):
                if name:
                    self.named_widgets[name] = widget
                self.widgets.add(widget)

        self.require_repaint()

    async def refresh_layout(self) -> None:
        await self.layout.mount_all(self)
        if not self.size:
            return

        width, height = self.console.size
        # virtual_width, virtual_height = self.virtual_size
        hidden, shown, resized = self.layout.reflow(
            self.console, width, height, self.scroll
        )
        self.virtual_size = self.layout.map.virtual_size
        self.app.refresh()

        for widget in hidden:
            widget.post_message_no_wait(events.Hide(self))
        for widget in shown:
            widget.post_message_no_wait(events.Show(self))

        send_resize = shown
        send_resize.update(resized)

        for widget, region in self.layout:
            if widget in send_resize:
                widget.post_message_no_wait(
                    events.Resize(self, region.width, region.height)
                )

    async def on_resize(self, event: events.Resize) -> None:
        self.size = Dimensions(event.width, event.height)
        await self.refresh_layout()

    def get_widget_at(self, x: int, y: int) -> tuple[Widget, Region]:
        return self.layout.get_widget_at(x, y)

    def get_style_at(self, x: int, y: int) -> Style:
        return self.layout.get_style_at(x, y)

    def get_widget_region(self, widget: Widget) -> Region:
        return self.layout.get_widget_region(widget)

    async def on_mount(self, event: events.Mount) -> None:
        async def watch_background(value: str) -> None:
            self.background = value

        watch(self.app, "background", watch_background)

    async def on_idle(self, event: events.Idle) -> None:
        if self.layout.check_update():
            self.layout.reset_update()
            await self.refresh_layout()

    async def _on_mouse_move(self, event: events.MouseMove) -> None:

        try:
            if self.app.mouse_captured:
                widget = self.app.mouse_captured
                region = self.get_widget_region(widget)
            else:
                widget, region = self.get_widget_at(event.x, event.y)
        except NoWidget:
            await self.app.set_mouse_over(None)
        else:
            await self.app.set_mouse_over(widget)
            await widget.forward_event(
                events.MouseMove(
                    self,
                    event.x - region.x,
                    event.y - region.y,
                    event.delta_x,
                    event.delta_y,
                    event.button,
                    event.shift,
                    event.meta,
                    event.ctrl,
                    screen_x=event.screen_x,
                    screen_y=event.screen_y,
                    style=event.style,
                )
            )

    async def forward_event(self, event: events.Event) -> None:

        if isinstance(event, (events.Enter, events.Leave)):
            await self.post_message(event)

        elif isinstance(event, events.MouseMove):
            event.style = self.get_style_at(event.screen_x, event.screen_y)
            await self._on_mouse_move(event)

        elif isinstance(event, events.MouseEvent):
            try:
                if self.app.mouse_captured:
                    widget = self.app.mouse_captured
                    region = self.get_widget_region(widget)
                else:
                    widget, region = self.get_widget_at(event.x, event.y)
            except NoWidget:
                pass
            else:
                if isinstance(event, events.MouseDown) and widget.can_focus:
                    await self.app.set_focus(widget)
                event.style = self.get_style_at(event.screen_x, event.screen_y)
                await widget.forward_event(event.offset(-region.x, -region.y))

        elif isinstance(event, (events.MouseScrollDown, events.MouseScrollUp)):
            try:
                widget, _region = self.get_widget_at(event.x, event.y)
            except NoWidget:
                return
            scroll_widget = widget or self.focused
            if scroll_widget is not None:
                await scroll_widget.forward_event(event)
        else:
            if self.focused is not None:
                await self.focused.forward_event(event)

    async def action_toggle(self, name: str) -> None:
        widget = self.named_widgets[name]
        widget.visible = not widget.visible
        await self.post_message(LayoutMessage(self))
