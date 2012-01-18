# -*- coding: utf-8 -*-
# This file is part of CairoSVG
# Copyright © 2010-2012 Kozea
#
# This library is free software: you can redistribute it and/or modify it under
# the terms of the GNU Lesser General Public License as published by the Free
# Software Foundation, either version 3 of the License, or (at your option) any
# later version.
#
# This library is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
# FOR A PARTICULAR PURPOSE.  See the GNU Lesser General Public License for more
# details.
#
# You should have received a copy of the GNU Lesser General Public License
# along with CairoSVG.  If not, see <http://www.gnu.org/licenses/>.

"""
Cairo surface creators.

"""

import cairo
import io
import math

from ..parser import Tree
from .colors import color
from .defs import gradient_or_pattern, parse_def
from .helpers import node_format, normalize
from .path import PATH_TAGS
from .tags import TAGS
from .units import size
from . import units


class Surface(object):
    """Abstract base class for CairoSVG surfaces.

    The ``width`` and ``height`` attributes are in device units (pixels for
    PNG, else points).

    """

    # Subclasses must either define this or override _create_surface()
    surface_class = None

    @classmethod
    def convert(cls, bytestring=None, **kwargs):
        """Convert a SVG document to the format for this class.

        Specify the input by passing one of these:

        :param bytestring: The SVG source as a byte-string.
        :param file_obj: A file-like object.
        :param url: A filename.

        And the output with:

        :param write_to: The filename of file-like object where to write the
                         output. If None or not provided, return a byte string.

        Only ``source`` can be passed as a positional argument, other
        parameters are keyword-only.

        """
        write_to = kwargs.pop('write_to', None)
        kwargs['bytestring'] = bytestring
        tree = Tree(**kwargs)
        if write_to is None:
            output = io.BytesIO()
        else:
            output = write_to
        cls(tree, output).finish()
        if write_to is None:
            return output.getvalue()

    def __init__(self, tree, output):
        """Create the surface from a filename or a file-like object.

        The rendered content is written to ``output`` which can be a filename,
        a file-like object, ``None`` (render in memory but do not write
        anything) or the built-in ``bytes`` as a marker.

        Call the ``.finish()`` method to make sure that the output is
        actually written.

        """
        self.cairo = None
        self.context = None
        self.cursor_position = 0, 0
        self.total_width = 0
        self.markers = {}
        self.gradients = {}
        self.patterns = {}
        self.paths = {}
        self.page_sizes = []
        self._old_parent_node = self.parent_node = None
        self.output = output
        width, height, viewbox = node_format(tree)
        # Actual surface dimensions: may be rounded on raster surfaces types
        self.cairo, self.width, self.height = self._create_surface(
            width * self.device_units_per_user_units,
            height * self.device_units_per_user_units)
        self.page_sizes.append((self.width, self.height))
        self.context = cairo.Context(self.cairo)
        # We must scale the context as the surface size is using physical units
        self.context.scale(
            self.device_units_per_user_units, self.device_units_per_user_units)
        # Initial, non-rounded dimensions
        self.set_context_size(width, height, viewbox)
        self.context.move_to(0, 0)
        self.draw_root(tree)

    @property
    def points_per_pixel(self):
        """Surface resolution."""
        return 1 / (units.DPI * units.UNITS["pt"])

    @property
    def device_units_per_user_units(self):
        """Ratio between Cairo device units and user units.

        Device units are points for everything but PNG, and pixels for
        PNG. User units are pixels.

        """
        return self.points_per_pixel

    def _create_surface(self, width, height):
        """Create and return ``(cairo_surface, width, height)``."""
        # self.surface_class should not be None when called here
        # pylint: disable=E1102
        cairo_surface = self.surface_class(self.output, width, height)
        # pylint: enable=E1102
        return cairo_surface, width, height

    def set_context_size(self, width, height, viewbox):
        """Set the context size."""
        if viewbox:
            x, y, x_size, y_size = viewbox
            x_ratio, y_ratio = width / x_size, height / y_size
            if x_ratio > y_ratio:
                self.context.translate((width - x_size * y_ratio) / 2, 0)
                self.context.scale(y_ratio, y_ratio)
                self.context.translate(-x, -y / y_ratio * x_ratio)
            elif x_ratio < y_ratio:
                self.context.translate(0, (height - y_size * x_ratio) / 2)
                self.context.scale(x_ratio, x_ratio)
                self.context.translate(-x / x_ratio * y_ratio, -y)
            else:
                self.context.scale(x_ratio, y_ratio)
                self.context.translate(-x, -y)

    def finish(self):
        """Read the surface content."""
        self.cairo.finish()

    def draw_root(self, node):
        """Draw the root ``node``."""
        self.draw(node)

    def draw(self, node, stroke_and_fill=True):
        """Draw ``node`` and its children."""
        # Do not draw defs
        if node.tag == "defs":
            for child in node.children:
                parse_def(self, child)
            return

        node.tangents = [None]
        node.pending_markers = []

        self._old_parent_node = self.parent_node
        self.parent_node = node

        self.context.save()
        self.context.move_to(size(node.get("x")), size(node.get("y")))

        # Transform the context according to the ``transform`` attribute
        if node.get("transform"):
            transformations = node["transform"].split(")")
            for transformation in transformations:
                for ttype in (
                        "scale", "translate", "matrix", "rotate", "skewX",
                        "skewY"):
                    if ttype in transformation:
                        transformation = transformation.replace(ttype, "")
                        transformation = transformation.replace("(", "")
                        transformation = normalize(transformation).strip()
                        transformation += " "
                        values = []
                        while transformation:
                            value, transformation = \
                                transformation.split(" ", 1)
                            values.append(size(value))
                        if ttype == "matrix":
                            matrix = cairo.Matrix(*values)
                            self.context.set_matrix(matrix)
                        elif ttype == "rotate":
                            matrix = self.context.get_matrix()
                            self.context.rotate(math.radians(float(values[0])))
                        elif ttype == "skewX":
                            matrix = self.context.get_matrix()
                            degree = math.radians(float(values[0]))
                            mtrx = cairo.Matrix(
                                matrix[0], matrix[1], matrix[2] + degree,
                                matrix[3], matrix[4], matrix[5])
                            self.context.set_matrix(mtrx)
                        elif ttype == "skewY":
                            matrix = self.context.get_matrix()
                            degree = math.radians(float(values[0]))
                            mtrx = cairo.Matrix(
                                matrix[0], matrix[1] + degree, matrix[2],
                                matrix[3], matrix[4], matrix[5])
                            self.context.set_matrix(mtrx)
                        else:
                            if len(values) == 1:
                                values = 2 * values
                            getattr(self.context, ttype)(*values)

        if node.tag in PATH_TAGS:
            # Set 1 as default stroke-width
            if not node.get("stroke-width"):
                node["stroke-width"] = "1"

        # Set node's drawing informations if the ``node.tag`` method exists
        line_cap = node.get("stroke-linecap")
        if line_cap == "square":
            self.context.set_line_cap(cairo.LINE_CAP_SQUARE)
        if line_cap == "round":
            self.context.set_line_cap(cairo.LINE_CAP_ROUND)

        join_cap = node.get("stroke-linejoin")
        if join_cap == "round":
            self.context.set_line_join(cairo.LINE_JOIN_ROUND)
        if join_cap == "bevel":
            self.context.set_line_join(cairo.LINE_JOIN_BEVEL)

        miter_limit = float(node.get("stroke-miterlimit", 4))
        self.context.set_miter_limit(miter_limit)

        if node.tag in TAGS:
            TAGS[node.tag](self, node)

        # Get stroke and fill opacity
        opacity = float(node.get("opacity", 1))
        stroke_opacity = opacity * float(node.get("stroke-opacity", 1))
        fill_opacity = opacity * float(node.get("fill-opacity", 1))

        # Manage dispaly and visibility
        display = node.get("display", "inline") != "none"
        visible = display and (node.get("visibility", "visible") != "hidden")

        if stroke_and_fill and visible:
            # Fill
            if "url(#" in node.get("fill", ""):
                gradient_or_pattern(self, node)
            else:
                if node.get("fill-rule") == "evenodd":
                    self.context.set_fill_rule(cairo.FILL_RULE_EVEN_ODD)
                self.context.set_source_rgba(
                    *color(node.get("fill", "black"), fill_opacity))
                self.context.fill_preserve()

            # Stroke
            self.context.set_line_width(size(node.get("stroke-width")))
            self.context.set_source_rgba(
                *color(node.get("stroke"), stroke_opacity))
            self.context.stroke()

        # Draw children
        if display:
            for child in node.children:
                self.draw(child, stroke_and_fill)

        if not node.root:
            # Restoring context is useless if we are in the root tag, it may
            # raise an exception if we have multiple svg tags
            self.context.restore()

        self.parent_node = self._old_parent_node


class MultipageSurface(Surface):
    """Abstract base class for surfaces that can handle multiple pages."""
    def draw_root(self, node):
        self.width = None
        self.height = None
        svg_children = [child for child in node.children if child.tag == 'svg']
        if svg_children:
            # Multi-page
            for page in svg_children:
                width, height, viewbox = node_format(page)
                # TODO: test this
                width *= self.device_units_per_user_units
                height *= self.device_units_per_user_units
                self.page_sizes.append((width, height))
                self.cairo.set_size(width, height)
                self.context.save()
                self.set_context_size(width, height, viewbox)
                self.draw(page)
                self.context.restore()
                self.cairo.show_page()
        else:
            self.draw(node)


class PDFSurface(MultipageSurface):
    """A surface that writes in PDF format."""
    surface_class = cairo.PDFSurface


class PSSurface(MultipageSurface):
    """A surface that writes in PostScript format."""
    surface_class = cairo.PSSurface


class PNGSurface(Surface):
    """A surface that writes in PNG format."""
    device_units_per_user_units = 1

    def _create_surface(self, width, height):
        """Create and return ``(cairo_surface, width, height)``."""
        width = int(width)
        height = int(height)
        cairo_surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, width, height)
        return cairo_surface, width, height

    def finish(self):
        """Read the PNG surface content."""
        if self.output is not None:
            self.cairo.write_to_png(self.output)
        return super(PNGSurface, self).finish()


class SVGSurface(Surface):
    """A surface that writes in SVG format.

    It may seem pointless to render SVG to SVG, but this can be used
    with ``output=None`` to get a vector-based single page cairo surface.

    """
    surface_class = cairo.SVGSurface
