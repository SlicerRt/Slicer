import os
import fnmatch
from __main__ import qt
from __main__ import ctk
from __main__ import vtk
from __main__ import slicer
import ColorBox
import EditUtil
# import MergeLabelMapBox
# import SegmentationBox

#########################################################
#
#
comment = """

  HelperBox is a wrapper around a set of Qt widgets and other
  structures to manage the slicer3 segmentation helper box.

# TODO :
"""
#
#########################################################

class HelperBox(object):

  def __init__(self, parent=None):

    self.editUtil = EditUtil.EditUtil()

    # mrml volume node instances
    self.master = None
    self.merge = None
    self.masterWhenMergeWasSet = None
    # string
    self.createMergeOptions = ""
    self.mergeNodeName = ""
    self.mergeVolumePostfix = "-label"
    self.segmentationPostfix = "-segmentation"
    # pairs of (node instance, observer tag number)
    self.observerTags = []
    # instance of a ColorBox
    self.colorBox = None
    # slicer helper class
    self.applicationLogic = slicer.app.applicationLogic()
    self.volumesLogic = slicer.modules.volumes.logic()
    self.colorLogic = slicer.modules.colors.logic()
    # qt model/view classes to track per-structure volumes
    self.structures = qt.QStandardItemModel()
    self.items = []
    self.brushes = []
    # widgets that are dynamically created on demand
    self.labelCreate = None
    self.labelSelect = None
    self.labelSelector = None
    # pseudo signals
    # - python callable that gets True or False
    self.mergeValidCommand = None
    self.selectCommand = None
    # mrml node for invoking command line modules
    self.CLINode = None

    if not parent:
      self.parent = slicer.qMRMLWidget()
      self.parent.setLayout(qt.QVBoxLayout())
      self.parent.setMRMLScene(slicer.mrmlScene)
      self.create()
      self.parent.show()
    else:
      self.parent = parent
      self.create()

  def onEnter(self):
    # new scene, node added or removed events
    tag = slicer.mrmlScene.AddObserver(slicer.vtkMRMLScene.NodeAddedEvent, self.updateStructures)
    self.observerTags.append( (slicer.mrmlScene, tag) )
    tag = slicer.mrmlScene.AddObserver(slicer.vtkMRMLScene.NodeRemovedEvent, self.updateStructures)
    self.observerTags.append( (slicer.mrmlScene, tag) )

  def onExit(self):
    for tagpair in self.observerTags:
      tagpair[0].RemoveObserver(tagpair[1])

  def cleanup(self):
    self.onExit()
    if self.colorBox:
      self.colorBox.cleanup()

  def newMerge(self):
    """create a merge volume for the current master even if one exists"""
    self.createMergeOptions = "new"
    self.labelCreateDialog()

  def createMerge(self):
    """create a merge volume for the current master"""
    if not self.master:
      # should never happen
      self.errorDialog( "Cannot create merge volume without master" )

    if self.createMergeOptions.find("new") >= 0:
      merge = None
    else:
      merge = self.mergeVolume()
    self.createMergeOptions = ""

    if not merge:
      merge = self.volumesLogic.CreateAndAddLabelVolume( slicer.mrmlScene, self.master, self.mergeNodeName )
      merge.GetDisplayNode().SetAndObserveColorNodeID( self.labelCreator.currentNodeID )
      self.setMergeVolume( merge )
    self.select(mergeVolume=merge)

  def select(self, masterVolume=None, mergeVolume=None):
    """select master volume - load merge volume if one with the correct name exists"""

    if masterVolume == None:
        masterVolume = self.masterSelector.currentNode()
    self.master = masterVolume
    self.merge = mergeVolume
    merge = self.mergeVolume()
    mergeText = "None"
    if merge:
      if not merge.IsA("vtkMRMLLabelMapVolumeNode"):
        self.errorDialog( "Error: selected merge label volume is not a label volume" )
      else:
        # make the source node the active background, and the label node the active label
        selectionNode = self.applicationLogic.GetSelectionNode()
        selectionNode.SetReferenceActiveVolumeID( self.master.GetID() )
        selectionNode.SetReferenceActiveLabelVolumeID( merge.GetID() )

        self.propagateVolumeSelection()
        mergeText = merge.GetName()
        self.merge = merge
    else:
      # the master exists, but there is no merge volume yet
      # bring up dialog to create a merge with a user-selected color node
      if self.master:
        self.labelCreateDialog()

    self.mergeName.setText( mergeText )
    self.updateStructures()

    if self.master and merge:
      warnings = self.volumesLogic.CheckForLabelVolumeValidity(self.master,self.merge)
      if warnings != "":
        warnings = "Geometry of master and merge volumes do not match.\n\n" + warnings
        self.errorDialog( "Warning: %s" % warnings )

    # trigger a modified event on the parameter node so that other parts of the GUI
    # (such as the EditColor) will know to update and enable themselves
    self.editUtil.getParameterNode().Modified()

    # make sure the selector is up to date
    self.masterSelector.setCurrentNode(self.master)

    if self.selectCommand:
      self.selectCommand()

  def propagateVolumeSelection(self):
    parameterNode = self.editUtil.getParameterNode()
    mode = int(parameterNode.GetParameter("propagationMode"))
    self.applicationLogic.PropagateVolumeSelection(mode, 0)

  def setVolumes(self,masterVolume,mergeVolume):
    """set both volumes at the same time - trick the callback into
    thinking that the merge volume is already set so it won't prompt for a new one"""
    self.masterWhenMergeWasSet = masterVolume
    self.select(masterVolume=masterVolume, mergeVolume=mergeVolume)

  def setMasterVolume(self,masterVolume):
    """select merge volume"""
    self.masterSelector.setCurrentNode( masterVolume )
    self.select()

  def setMergeVolume(self,mergeVolume=None):
    """select merge volume"""
    if self.master:
      if mergeVolume:
        self.merge = mergeVolume
        if self.labelSelector:
          self.labelSelector.setCurrentNode( self.merge )
      else:
        if self.labelSelector:
          self.merge = self.labelSelector.currentNode()
      self.masterWhenMergeWasSet = self.master
      self.select(masterVolume=self.master,mergeVolume=mergeVolume)

  def mergeVolume(self):
    """select merge volume"""
    if not self.master:
      return None

    # if we already have a merge and the master hasn't changed, use it
    if self.merge and self.master == self.masterWhenMergeWasSet:
      mergeNode = slicer.mrmlScene.GetNodeByID( self.merge.GetID() )
      if mergeNode and mergeNode != "":
        return self.merge

    self.merge = None
    self.masterWhenMergeWasSet = None

    # otherwise pick the merge based on the master name
    # - either return the merge volume or empty string
    masterName = self.master.GetName()
    mergeName = masterName+self.mergeVolumePostfix
    self.merge = self.getNodeByName( mergeName, className=self.master.GetClassName() )
    return self.merge

  def labelCreateDialog(self):
    """label create dialog"""

    if not self.labelCreate:
      self.labelCreate = qt.QDialog(slicer.util.mainWindow())
      self.labelCreate.objectName = 'EditorLabelCreateDialog'
      self.labelCreate.setLayout( qt.QVBoxLayout() )

      self.colorPromptLabel = qt.QLabel()
      self.labelCreate.layout().addWidget( self.colorPromptLabel )

      self.mergeTypeSelectorFrame = qt.QFrame()
      self.mergeTypeSelectorFrame.objectName = 'MergeTypeSelectorFrame'
      self.mergeTypeSelectorFrame.setLayout( qt.QVBoxLayout() )
      self.labelCreate.layout().addWidget( self.mergeTypeSelectorFrame )

      self.nodeTypeLabel = qt.QLabel()
      self.nodeTypeLabel.text = 'Create a node of type'
      self.mergeTypeSelectorFrame.layout().addWidget( self.nodeTypeLabel )
      self.segmentationRadioButton = qt.QRadioButton('segmentation')
      self.labelMapRadioButton = qt.QRadioButton('label map volume')
      self.mergeTypeSelectorFrame.layout().addWidget(self.segmentationRadioButton)
      self.mergeTypeSelectorFrame.layout().addWidget(self.labelMapRadioButton)
      self.mergeNodeNameLabel = qt.QLabel()
      self.mergeTypeSelectorFrame.layout().addWidget( self.mergeNodeNameLabel )
      self.segmentationRadioButton.connect("toggled(bool)", self.onMergeTypeChanged)
      self.segmentationRadioButton.checked = True

      self.colorTableSelectorFrame = qt.QFrame()
      self.colorTableSelectorFrame.objectName = 'ColorSelectorFrame'
      self.colorTableSelectorFrame.setLayout( qt.QHBoxLayout() )
      self.labelCreate.layout().addWidget( self.colorTableSelectorFrame )

      self.labelCreatorLabel = qt.QLabel()
      self.labelCreatorLabel.setText( "Color Table: " )
      self.colorTableSelectorFrame.layout().addWidget( self.labelCreatorLabel )

      self.labelCreator = slicer.qMRMLColorTableComboBox()
      # TODO
      self.labelCreator.nodeTypes = ("vtkMRMLColorNode", "")
      self.labelCreator.hideChildNodeTypes = ("vtkMRMLDiffusionTensorDisplayPropertiesNode", "vtkMRMLProceduralColorNode", "")
      self.labelCreator.addEnabled = False
      self.labelCreator.removeEnabled = False
      self.labelCreator.noneEnabled = False
      self.labelCreator.selectNodeUponCreation = True
      self.labelCreator.showHidden = True
      self.labelCreator.showChildNodeTypes = True
      self.labelCreator.setMRMLScene( slicer.mrmlScene )
      self.labelCreator.setToolTip( "Pick the table of structures you wish to edit" )
      self.labelCreate.layout().addWidget( self.labelCreator )

      self.colorButtonFrame = qt.QFrame()
      self.colorButtonFrame.objectName = 'ColorButtonFrame'
      self.colorButtonFrame.setLayout( qt.QHBoxLayout() )
      self.labelCreate.layout().addWidget( self.colorButtonFrame )

      self.labelCreateDialogApply = qt.QPushButton("Apply", self.colorButtonFrame)
      self.labelCreateDialogApply.objectName = 'LabelCreateDialogApply'
      self.labelCreateDialogApply.setToolTip( "Use currently selected color node." )
      self.colorButtonFrame.layout().addWidget(self.labelCreateDialogApply)

      self.labelCreateDialogCancel = qt.QPushButton("Cancel", self.colorButtonFrame)
      self.labelCreateDialogCancel.objectName = 'LabelCreateDialogCancel'
      self.labelCreateDialogCancel.setToolTip( "Cancel current operation." )
      self.colorButtonFrame.layout().addWidget(self.labelCreateDialogCancel)

      self.labelCreateDialogApply.connect("clicked()", self.onLabelCreateDialogApply)
      self.labelCreateDialogCancel.connect("clicked()", self.labelCreate.hide)

    # pick the default editor LUT for the user
    defaultID = self.colorLogic.GetDefaultEditorColorNodeID()
    defaultNode = slicer.mrmlScene.GetNodeByID(defaultID)
    if defaultNode:
      self.labelCreator.setCurrentNode( defaultNode )

    self.colorPromptLabel.text = "Create a merge label map or a segmentation for selected master volume %s.\nSelect the color table node that will be used for segmentation labels." %(self.master.GetName())
    self.labelCreate.show()

  def onMergeTypeChanged(self, state):
    if state == True:
      self.mergeNodeName = self.master.GetName() + self.segmentationPostfix
    else:
      self.mergeNodeName = self.master.GetName() + self.mergeVolumePostfix
    self.mergeNodeNameLabel.text = 'named %s' % (self.mergeNodeName)

  # colorSelect callback (slot)
  def onLabelCreateDialogApply(self):
    self.createMerge()
    self.labelCreate.hide()

  def labelSelectDialog(self):
    """label table dialog"""

    if not self.labelSelect:
      self.labelSelect = qt.QFrame()
      self.labelSelect.setLayout( qt.QVBoxLayout() )

      self.labelPromptLabel = qt.QLabel()
      self.labelSelect.layout().addWidget( self.labelPromptLabel )


      self.labelSelectorFrame = qt.QFrame()
      self.labelSelectorFrame.setLayout( qt.QHBoxLayout() )
      self.labelSelect.layout().addWidget( self.labelSelectorFrame )

      self.labelSelectorLabel = qt.QLabel()
      self.labelPromptLabel.setText( "Label Map: " )
      self.labelSelectorFrame.layout().addWidget( self.labelSelectorLabel )

      self.labelSelector = slicer.qMRMLNodeComboBox()
      self.labelSelector.nodeTypes = ( "vtkMRMLLabelMapVolumeNode", "" )
      self.labelSelector.selectNodeUponCreation = False
      self.labelSelector.addEnabled = False
      self.labelSelector.noneEnabled = False
      self.labelSelector.removeEnabled = False
      self.labelSelector.showHidden = False
      self.labelSelector.showChildNodeTypes = False
      self.labelSelector.setMRMLScene( slicer.mrmlScene )
      self.labelSelector.setToolTip( "Pick the label map or segmentation to edit" )
      self.labelSelectorFrame.layout().addWidget( self.labelSelector )

      self.labelButtonFrame = qt.QFrame()
      self.labelButtonFrame.setLayout( qt.QHBoxLayout() )
      self.labelSelect.layout().addWidget( self.labelButtonFrame )

      self.labelDialogApply = qt.QPushButton("Apply", self.labelButtonFrame)
      self.labelDialogApply.setToolTip( "Use currently selected label or segmentation node." )
      self.labelButtonFrame.layout().addWidget(self.labelDialogApply)

      self.labelDialogCancel = qt.QPushButton("Cancel", self.labelButtonFrame)
      self.labelDialogCancel.setToolTip( "Cancel current operation." )
      self.labelButtonFrame.layout().addWidget(self.labelDialogCancel)

      self.labelButtonFrame.layout().addStretch(1)

      self.labelDialogCreate = qt.QPushButton("Create New...", self.labelButtonFrame)
      self.labelDialogCreate.setToolTip( "Cancel current operation." )
      self.labelButtonFrame.layout().addWidget(self.labelDialogCreate)

      self.labelDialogApply.connect("clicked()", self.onLabelDialogApply)
      self.labelDialogCancel.connect("clicked()", self.labelSelect.hide)
      self.labelDialogCreate.connect("clicked()", self.onLabelDialogCreate)

    self.labelPromptLabel.setText( "Select existing label map volume to edit." )
    p = qt.QCursor().pos()
    self.labelSelect.setGeometry(p.x(), p.y(), 400, 200)
    self.labelSelect.show()

  # labelSelect callbacks (slots)
  def onLabelDialogApply(self):
    self.setMergeVolume(self.labelSelector.currentNode())
    self.labelSelect.hide()

  def onLabelDialogCreate(self):
    self.newMerge()
    self.labelSelect.hide()

  def getNodeByName(self, name, className=None):
    """get the first MRML node that has the given name
    - use a regular expression to match names post-pended with addition characters
    - optionally specify a classname that must match
    """
    nodes = slicer.util.getNodes(name+'*')
    for nodeName in nodes.keys():
      if not className:
        return (nodes[nodeName]) # return the first one
      else:
        if nodes[nodeName].IsA(className):
          return (nodes[nodeName])
    return None

  def errorDialog(self, message):
    self.dialog = qt.QErrorMessage()
    self.dialog.setWindowTitle("Editor")
    self.dialog.showMessage(message)

  def confirmDialog(self, message):
    result = qt.QMessageBox.question(slicer.util.mainWindow(),
                    'Editor', message,
                    qt.QMessageBox.Ok, qt.QMessageBox.Cancel)
    return result == qt.QMessageBox.Ok

  def statusText(self, text):
    slicer.util.showStatusMessage( text,1000 )
