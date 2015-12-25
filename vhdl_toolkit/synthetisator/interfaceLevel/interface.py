import python_toolkit
from python_toolkit.arrayQuery import single
from python_toolkit.stringUtils import matchIgnorecase
from vivado_toolkit.ip_packager.busInterface import InterfaceIncompatibilityExc
from copy import deepcopy
from vhdl_toolkit.types import DIRECTION, INTF_DIRECTION
from hls_toolkit.errors import UnimplementedErr
from vhdl_toolkit.synthetisator.interfaceLevel.buildable import Buildable
from build.lib.vhdl_toolkit.synthetisator.param import Param

# class Param():
#    def __init__(self, val):
#        self.val = val

class Interface(Buildable):
    """
    @cvar cvar:  NAME_SEPARATOR: separator for nested interface names   
    @cvar _subInterfaces: Dict of sub interfaces (name : interf) 
    @ivar _subInterfaces: deep copy of class _subInterfaces
    @ivar _src: Driver for this interface
    @ivar _desctinations: Interfaces for which this interface is driver
    @ivar _isExtern: If true synthetisator sets it as external port of unit
    @ivar _originEntityPort: entityPort for which was this interface created
    @ivar _originSigLvlUnit: VHDL unit for which was this interface created
    """
    NAME_SEPARATOR = "_"  
    def __init__(self, *destinations, masterDir=DIRECTION.OUT, src=None, isExtern=False):
        """
        @param *destinations: interfaces connected to this interface
        @param src:  interface whitch is master for this interface (if None isExtern has to be true)
        @param hasExter: if true this interface is specified as interface outside of this unit  
        """
        
        #build all interface classes for this interface
        self.__class__._builded()
        self._masterDir = masterDir
        
        # deepcopy interfaces from class
        self._subInterfaces = deepcopy(self.__class__._subInterfaces, {})
        for propName, prop in self._subInterfaces.items():
            setattr(self, propName, prop)
            prop._parent = self
            prop._name = propName
            self._subInterfaces[propName] = getattr(self, propName)
        
        if isExtern and not src:
            self._src = None
            self._direction = INTF_DIRECTION.MASTER
        else:
            self._src = src
            self._direction = INTF_DIRECTION.SLAVE
        self._isExtern = isExtern
            
        self._destinations = list(destinations)
        self._isExtern = isExtern
    
    def _propagateSrc(self):
        if self._src:
            self._src._destinations.append(self)
                 
    @classmethod
    def _build(cls):
        """
        create a _subInterfaces from class properties
        """
        assert(not cls._isBuild())
        assert(cls != Interface) # only derived classes should be builded
        cls._subInterfaces = {}
        cls._params = {}
        
        #copy from bases
        for c in cls.__bases__:
            if issubclass(c, Interface) and c != Interface: # only derived classes should be builded
                c._builded()
                for intfName, intf in c._subInterfaces.items():
                    cls._subInterfaces[intfName] = intf
        
        for propName, prop in vars(cls).items():
            pCls = prop.__class__
            if issubclass(pCls, Interface):
                pCls._builded()
                cls._subInterfaces[propName] = prop
            elif issubclass(pCls, Param):
                cls._params[propName] = prop
        cls._clsIsBuild = True
        
    def _rmSignals(self, rmConnetions=True):
        """Remove all signals from this interface (used after unit is synthetized
         and its parent is connecting its interface to this unit)"""
        if hasattr(self, "_sig"):
            del self._sig
        for _, i in self._subInterfaces.items():
            i._rmSignals()
        if rmConnetions:
            self._src = None
            self._destinations = []
            
    @classmethod
    def _extractPossibleInstanceNames(cls, entity, prefix=""):
        """
        @return: iterator over unit ports witch probably matches with this interface
        """        
        assert(cls._clsIsBuild)
        if cls._subInterfaces:
            firstIntfName, firstIntfPort = list(cls._subInterfaces.items())[0]
            assert(not firstIntfPort._subInterfaces)  # only one level of hierarchy is supported for now
        else:
            firstIntfName = ""
            
        for p in entity.port:
            if not hasattr(p, "_interface") and p.name.lower().endswith(prefix + firstIntfName):
                prefixLen = len(prefix) + len(firstIntfName)
                if prefixLen == 0:
                    yield p.name
                else:
                    yield p.name[:-prefixLen]
                
    def _unExtrac(self):
        """Revent extracting process for this interface"""
        for _, intfConfMap in self._subInterfaces.items():
            if hasattr(intfConfMap, "_originEntityPort"):
                if hasattr(intfConfMap._originEntityPort, "_interface"):
                    del intfConfMap._originEntityPort._interface
                del intfConfMap._originEntityPort
                del intfConfMap._originSigLvlUnit
    
      
    def _tryToExtractByName(self, prefix, sigLevelUnit):
        """
        @return: self if extraction was successful
        @raise InterfaceIncompatibilityExc: if this interface with this prefix does not fit to this entity 
        """
        allDirMatch = True
        noneDirMatch = True
        if self._subInterfaces:
            try:
                for intfName, intf in self._subInterfaces.items():
                    assert(intf._name == intfName)
                    intf._tryToExtractByName(prefix + intfName, sigLevelUnit)
                    dirMatches = intf._originEntityPort.direction == intf._masterDir
                    allDirMatch = allDirMatch and dirMatches
                    noneDirMatch = noneDirMatch  and not dirMatches     
            except InterfaceIncompatibilityExc as e:
                for intfName, intf in self._subInterfaces.items():
                    intf._unExtrac()
                raise e
        else:
            try:
                self._originEntityPort = single(sigLevelUnit.entity.port, lambda p : matchIgnorecase(p.name, prefix))
                self._originEntityPort._interface = self
                self._originSigLvlUnit = sigLevelUnit
                dirMatches = self._originEntityPort.direction == self._masterDir
                if dirMatches:
                    self._direction = DIRECTION.asIntfDirection(self._masterDir)
                else:
                    self._direction = DIRECTION.asIntfDirection(DIRECTION.oposite(self._masterDir)) 
            except python_toolkit.arrayQuery.NoValueExc:
                self._unExtrac()
                raise InterfaceIncompatibilityExc("Missing " + prefix)
        if allDirMatch:
            self._direction = INTF_DIRECTION.MASTER
        elif noneDirMatch:
            self._direction = INTF_DIRECTION.SLAVE
        else:
            self._unExtrac()
            raise InterfaceIncompatibilityExc("Direction mismatch")
        return self
    
    @classmethod        
    def _tryToExtract(cls, sigLevelUnit):
        """
        @return: iterator over tuples (interface name. extracted interface)
        """
        cls._builded()
        for name in cls._extractPossibleInstanceNames(sigLevelUnit.entity):
            try:
                intf = cls(isExtern=True)._tryToExtractByName(name, sigLevelUnit)
                if name.endswith('_'):
                    name = name[:-1]  # trim _
                yield (name, intf) 
            except InterfaceIncompatibilityExc:
                pass
                   
    def _connectTo(self, master):
        """
        connect to another interface interface 
        works like self <= master in VHDL
        """
        if self._subInterfaces:
            for nameIfc, ifc in self._subInterfaces.items():
                mIfc = master._subInterfaces[nameIfc]
                
                if (ifc._direction == INTF_DIRECTION.MASTER and ifc._masterDir == DIRECTION.OUT) \
                    or (ifc._direction == INTF_DIRECTION.SLAVE and ifc._masterDir == DIRECTION.IN):
                    mIfc._connectTo(ifc)
                elif (ifc._direction == INTF_DIRECTION.SLAVE and ifc._masterDir == DIRECTION.OUT) \
                    or (ifc._direction == INTF_DIRECTION.MASTER and ifc._masterDir == DIRECTION.IN):
                    ifc._connectTo(mIfc)
                else:
                    raise Exception("Interface direction improperly configured")
        else:
            if self._isExtern:
                assert(self._direction == INTF_DIRECTION.SLAVE) # slave for outside master for inside 
            self._sig.assignFrom(master._sig)
    
    def _propagateConnection(self):
        """
        Propagate connections from interface instance to all subinterfaces
        """
        for _, suIntf in self._subInterfaces.items():
            suIntf._propagateConnection()
        for d in self._destinations:
            d._connectTo(self)
    
    def _signalsForInterface(self, context, prefix):
        """
        generate _sig for each interface which has no subinterface
        if already has _sig return it instead
        """
        if self._subInterfaces:
            sigs = []
            for name, ifc in self._subInterfaces.items():
                sigs.extend(ifc._signalsForInterface(context, prefix + self.NAME_SEPARATOR + name))
            return sigs  
        else:
            if hasattr(self, '_sig'):
                return [self._sig]
            else:
                s = context.sig(prefix, self._width)
                s._interface = self
                self._sig = s
                
                if hasattr(self, '_originEntityPort'):
                    self._sig.connectToPortItem(self._originSigLvlUnit, self._originEntityPort)
                return [s]
    
    def _getFullName(self):
        name = ""
        tmp = self
        while hasattr(tmp, "_parent"):
            if hasattr(tmp, "_name"):
                n = tmp._name
            else:
                n = ''
            if name == '':
                name = n
            else:
                name = n + '.' + name
            tmp = tmp._parent
        return name
    
    def _reverseDirection(self):
        self._direction = INTF_DIRECTION.oposite(self._direction)
        for _, intf in self._subInterfaces.items():
            intf._reverseDirection()
            
    def __repr__(self):
        s = [self.__class__.__name__]
        s.append("name=%s" % self._getFullName())
        if hasattr(self, '_width'):
            s.append("_width=%s" % str(self._width))
        if hasattr(self, '_masterDir'):
            s.append("_masterDir=%s" % str(self._masterDir))
        return "<%s>" % (', '.join(s) )
    
    
    
    