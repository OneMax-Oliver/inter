# Copyright 2015 Google Inc. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.


import configparser
import os
import sys
import math

from booleanOperations import BooleanOperationManager

from cu2qu.ufo import fonts_to_quadratic
from fontTools.misc.transform import Transform
from fontParts.world import OpenFont
from ufo2ft import compileOTF, compileTTF

from fontbuild.decomposeGlyph import decomposeGlyph
from fontbuild.features import readFeatureFile, writeFeatureFile
from fontbuild.generateGlyph import generateGlyph
from fontbuild.instanceNames import setInfoRF
from fontbuild.italics import italicizeGlyph
from fontbuild.markFeature import RobotoMarkWriter, RobotoKernWriter
from fontbuild.mitreGlyph import mitreGlyph
from fontbuild.mix import Mix,Master,narrowFLGlyph


class FontProject:

    def __init__(self, basefont, basedir, configfile, buildTag=''):
        self.basefont = basefont
        self.basedir = basedir
        self.config = configparser.RawConfigParser()
        self.configfile = os.path.join(self.basedir, configfile)
        self.config.read(self.configfile)
        self.buildTag = buildTag

        self.diacriticList = [
            line.strip() for line in self.openResource("diacriticfile")
            if not line.startswith("#")]
        self.adobeGlyphList = dict(
            line.split(";") for line in self.openResource("agl_glyphlistfile")
            if not line.startswith("#"))
        self.glyphOrder = self.openResource("glyphorder")

        # map exceptional glyph names in Roboto to names in the AGL
        roboNames = (
            ('Obar', 'Ocenteredtilde'), ('obar', 'obarred'),
            ('eturn', 'eturned'), ('Iota1', 'Iotaafrican'))
        for roboName, aglName in roboNames:
            self.adobeGlyphList[roboName] = self.adobeGlyphList[aglName]

        self.builddir = "out"
        self.decompose = self.config.get("glyphs","decompose").split()
        self.predecompose = set(self.config.get("glyphs","predecompose").split())
        self.predecompose_auto = 1  # unless 0, automatically populate predecompose
        self.lessItalic = set(self.config.get("glyphs","lessitalic").split())
        self.deleteList = set(self.config.get("glyphs","delete").split())
        self.noItalic = set(self.config.get("glyphs","noitalic").split())

        self.buildOTF = False
        self.compatible = False
        self.generatedFonts = []

    def openResource(self, name):
        with open(os.path.join(
                self.basedir, self.config.get("res", name))) as resourceFile:
            resource = resourceFile.read()
        return resource.splitlines()

    def generateOutputPath(self, font, ext):
        family = font.info.familyName.replace(" ", "")
        style = font.info.styleName.replace(" ", "")
        path = os.path.join(self.basedir, self.builddir, family + ext.upper())
        if not os.path.exists(path):
            os.makedirs(path)
        return os.path.join(path, "%s-%s.%s" % (family, style, ext))

    def generateFont(self, mix, names, italic=False, swapSuffixes=None, stemWidth=185,
                     italicMeanYCenter=-825, scaleX=1, panose=[]):

        n = names.split("/")
        log("---------------------\n%s, %s\n----------------------" %(n[0],n[1]))

        if isinstance( mix, Mix):
            log(">> Mixing masters")
            f = mix.generateFont(self.basefont, self.deleteList)
        else:
            f = mix.copy()
            deleteGlyphs(f, self.deleteList)

        if italic == True:
            log(">> Italicizing")
            i = 0
            for g in f:
                decomposeGlyph(f, g)
                removeGlyphOverlap(g)

            for g in f:
                i += 1
                if i % 10 == 0: print(g.name)

                if g.name not in self.noItalic:
                    if g.name in self.lessItalic:
                        italicizeGlyph(f, g, 10, stemWidth=stemWidth,
                                       meanYCenter=italicMeanYCenter,
                                       scaleX=scaleX)
                    else:
                        italicizeGlyph(f, g, 12, stemWidth=stemWidth,
                                       meanYCenter=italicMeanYCenter,
                                       scaleX=scaleX)

            # set the oblique flag in fsSelection
            f.info.openTypeOS2Selection.append(9)

        if swapSuffixes != None:
            for swap in swapSuffixes:
                swapList = [g.name for g in f if g.name.endswith(swap)]
                for gname in swapList:
                    print(gname)
                    swapContours(f, gname.replace(swap,""), gname)
        
        if self.predecompose_auto == 1:
            self.predecompose_auto = 2
            for g in self.basefont:
                if len(g.components) > 0:
                    self.predecompose.add(g.name)

        for gname in self.predecompose:
            if gname in f:
                decomposeGlyph(f, f[gname])

        log(">> Generating glyphs")
        generateGlyphs(f, self.diacriticList, self.adobeGlyphList)
        # log(">> Reading features")
        # readFeatureFile(f, f.features.text)

        # adjust width of italic glyphs
        if italic == True:
            widthAdjustment = -8
            if widthAdjustment != 0:
                leftAdjustment = math.floor(widthAdjustment / 2)
                rightAdjustment = math.ceil(widthAdjustment / 2)
                for g in f:
                    if g.name not in self.noItalic:
                        if g.width != 0:
                            if g.box is None:
                                g.width += widthAdjustment
                            else:
                                newLeftMargin = int(g.leftMargin + leftAdjustment)
                                newRightMargin = int(g.rightMargin + rightAdjustment)
                                g.leftMargin  = newLeftMargin
                                g.rightMargin = newRightMargin

        log(">> Decomposing")
        # for g in f:
        #     if len(g.components) > 0:
        #         decomposeGlyph(f, g)
        for gname in self.decompose:
            if gname in f:
                decomposeGlyph(f, f[gname])

        copyrightHolderName = ''
        if self.config.has_option('main', 'copyrightHolderName'):
            copyrightHolderName = self.config.get('main', 'copyrightHolderName')

        def getcfg(name, fallback=''):
            if self.config.has_option('main', name):
                return self.config.get('main', name)
            else:
                return fallback

        setInfoRF(f, n, {
            'foundry':      getcfg('foundry'),
            'foundryURL':   getcfg('foundryURL'),
            'designer':     getcfg('designer'),
            'copyrightHolderName': getcfg('copyrightHolderName'),
            'build':        self.buildTag,
            'version':      getcfg('version'),
            'license':      getcfg('license'),
            'licenseURL':   getcfg('licenseURL'),
            'italicAngle':  float(getcfg('italicAngle', '-12')),
        }, panose)

        if not self.compatible:
            cleanCurves(f)
        # deleteGlyphs(f, self.deleteList)

        log(">> Generating font files")
        ufoName = self.generateOutputPath(f, "ufo")
        f.save(ufoName)
        self.generatedFonts.append(ufoName)

        # filter glyphorder -- only include glyphs that exists in font
        glyphOrder = []
        seenGlyphNames = set()
        missingGlyphs = []
        for glyphName in self.glyphOrder:
            if glyphName in f:
                if glyphName in seenGlyphNames:
                    raise Exception('Duplicate glyphname %r in glyphorder' % glyphName)
                seenGlyphNames.add(glyphName)
                glyphOrder.append(glyphName)

        if self.buildOTF:
            newFont = OpenFont(ufoName, showInterface=False)
            otfName = self.generateOutputPath(f, "otf")
            log(">> Generating OTF file %s" % otfName)
            saveOTF(newFont, otfName, glyphOrder)

    def generateTTFs(self):
        """Build TTF for each font generated since last call to generateTTFs."""

        fonts = [OpenFont(ufo, showInterface=False) for ufo in self.generatedFonts]
        self.generatedFonts = []

        log(">> Converting curves to quadratic")
        # using a slightly higher max error (e.g. 0.0025 em), dots will have
        # fewer control points and look noticeably different
        max_err = 0.001
        if self.compatible:
            fonts_to_quadratic(fonts, max_err_em=max_err, dump_stats=True, reverse_direction=True)
        else:
            for font in fonts:
                fonts_to_quadratic([font], max_err_em=max_err, dump_stats=True, reverse_direction=True)

        for font in fonts:
            ttfName = self.generateOutputPath(font, "ttf")
            log(">> Generating TTF file %s" % ttfName)
            log(os.path.basename(ttfName))
            glyphOrder = [n for n in self.glyphOrder if n in font]
            saveOTF(font, ttfName, glyphOrder, truetype=True)


# def transformGlyphMembers(g, m):
#     g.width = int(g.width * m.a)
#     g.Transform(m)
#     for a in g.anchors:
#         p = Point(a.p)
#         p.Transform(m)
#         a.p = p
#     for c in g.components:
#         # Assumes that components have also been individually transformed
#         p = Point(0,0)
#         d = Point(c.deltas[0])
#         d.Transform(m)
#         p.Transform(m)
#         d1 = d - p
#         c.deltas[0].x = d1.x
#         c.deltas[0].y = d1.y
#         s = Point(c.scale)
#         s.Transform(m)
#         #c.scale = s


def swapContours(f,gName1,gName2):
    try:
        g1 = f[gName1]
        g2 = f[gName2]
    except KeyError:
        log("swapGlyphs failed for %s %s" % (gName1, gName2))
        return
    g3 = g1.copy()

    while g1.contours:
        g1.removeContour(0)
    for contour in g2.contours:
        g1.appendContour(contour)
    g1.width = g2.width

    while g2.contours:
        g2.removeContour(0)
    for contour in g3.contours:
        g2.appendContour(contour)
    g2.width = g3.width


def log(msg):
    print(msg)


def generateGlyphs(f, glyphNames, glyphList={}):
    log(">> Generating diacritics")
    glyphnames = [gname for gname in glyphNames if not gname.startswith("#") and gname != ""]

    for glyphName in glyphNames:
        generateGlyph(f, glyphName, glyphList)


def deleteGlyphs(f, deleteList):
    for name in deleteList:
        if name in f:
            f.removeGlyph(name)


def cleanCurves(f):
    log(">> Removing overlaps")
    for g in f:
        if len(g.components) > 0:
            decomposeGlyph(f, g)
        removeGlyphOverlap(g)

    # log(">> Mitring sharp corners")
    # for g in f:
    #     mitreGlyph(g, 3., .7)

    # log(">> Converting curves to quadratic")
    # for g in f:
    #     glyphCurvesToQuadratic(g)


def removeGlyphOverlap(g):
    """Remove overlaps in contours from a glyph."""
    # Note: Although it theoretically would be more efficient to first check
    # if contours has overlap before applying clipping, boolean ops and
    # re-drawing the shapes, the booleanOperations library's getIntersections
    # function adds more overhead in the real world, compared to bluntly
    # computing the union for every single glyph.
    #
    # If we can find a really cheap/efficient way to check if there's any
    # overlap, then we should do that before going ahead and computing the
    # union. Keep in mind that some glyphs have just a single contour that
    # intersects itself, for instance "e".

    contours = g.contours
    g.clearContours()
    BooleanOperationManager.union(contours, g.getPointPen())


def saveOTF(font, destFile, glyphOrder, truetype=False):
    """Save a RoboFab font as an OTF binary using ufo2fdk."""

    if truetype:
        otf = compileTTF(
            font,
            # featureCompilerClass=RobotoFeatureCompiler,
            kernWriter=RobotoKernWriter,
            glyphOrder=glyphOrder,
            convertCubics=False,
            useProductionNames=False)
    else:
        # ufo,
        # preProcessorClass=OTFPreProcessor,
        # outlineCompilerClass=OutlineOTFCompiler,
        # featureCompilerClass=None,
        # featureWriters=None,
        # glyphOrder=None,
        # useProductionNames=None,
        # optimizeCFF=True,
        # roundTolerance=None,
        # removeOverlaps=False,
        # overlapsBackend=None,
        # inplace=False,
        otf = compileOTF(
            font,
            # featureCompilerClass=RobotoFeatureCompiler,
            # kernWriter=RobotoKernWriter,
            featureWriters=[RobotoKernWriter, RobotoMarkWriter],
            glyphOrder=glyphOrder,
            useProductionNames=False)
    otf.save(destFile)
