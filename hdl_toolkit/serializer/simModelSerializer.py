from hdl_toolkit.bitmask import Bitmask
from hdl_toolkit.hdlObjects.operatorDefs import AllOps
from hdl_toolkit.hdlObjects.statements import IfContainer, \
    SwitchContainer, WhileContainer, WaitStm
from hdl_toolkit.hdlObjects.types.array import Array
from hdl_toolkit.hdlObjects.types.bits import Bits
from hdl_toolkit.hdlObjects.types.defs import BOOL, BIT
from hdl_toolkit.hdlObjects.types.enum import Enum
from hdl_toolkit.hdlObjects.types.hdlType import HdlType, InvalidVHDLTypeExc
from hdl_toolkit.hdlObjects.types.typeCast import toHVal
from hdl_toolkit.hdlObjects.value import Value
from hdl_toolkit.serializer.exceptions import SerializerException
from hdl_toolkit.serializer.nameScope import LangueKeyword, NameScope
from hdl_toolkit.synthesizer.interfaceLevel.unitFromHdl import UnitFromHdl
from hdl_toolkit.synthesizer.param import Param, evalParam
from hdl_toolkit.synthesizer.rtlLevel.mainBases import RtlSignalBase
from python_toolkit.arrayQuery import where
from hdl_toolkit.hdlObjects.types.sliceVal import SliceVal
from  keyword import kwlist
from jinja2.loaders import PackageLoader
from jinja2.environment import Environment
from hdl_toolkit.serializer.utils import maxStmId
from hdl_toolkit.hdlObjects.types.boolean import Boolean
from hdl_toolkit.hdlObjects.types.integer import Integer
from hdl_toolkit.hdlObjects.types.string import String

       
opPrecedence = {AllOps.NOT : 4,
                AllOps.EVENT: 1,
                AllOps.RISING_EDGE: 1,
                AllOps.DIV: 4,
                AllOps.ADD : 5,
                AllOps.SUB: 5,
                AllOps.MUL: 4,
                AllOps.XOR: 9,
                AllOps.EQ: 11,
                AllOps.NEQ: 11,
                AllOps.AND_LOG: 8,
                AllOps.OR_LOG: 10,
                AllOps.DOWNTO: 1,
                AllOps.GREATERTHAN: 11,
                AllOps.LOWERTHAN: 11,
                AllOps.CONCAT: 1,
                AllOps.INDEX: 1,
                AllOps.TERNARY: 1,
                AllOps.CALL: 1,
                }

env = Environment(loader=PackageLoader('hdl_toolkit', 'serializer/templates_simModel'))
unitTmpl = env.get_template('modelCls.py')
processTmpl = env.get_template('process.py')

indent = "    "        
        

class SimModelSerializer():
    __keywords_dict = {kw: LangueKeyword() for kw in kwlist}
    __keywords_dict.update({'sim': LangueKeyword(),
                            'self': LangueKeyword()})
    
    @classmethod
    def getBaseNameScope(cls):
        s = NameScope(True)
        s.setLevel(1)
        s[0].update(cls.__keywords_dict)
        return s
    
    formater = lambda s: s
    
    @classmethod
    def asHdl(cls, obj):
        if isinstance(obj, UnitFromHdl):
            raise NotImplementedError()
        elif isinstance(obj, RtlSignalBase):
            return cls.SignalItem(obj)
        elif isinstance(obj, Value):
            return cls.Value(obj)
        else:
            try:
                serFn = getattr(cls, obj.__class__.__name__)
            except AttributeError:
                raise NotImplementedError("Not implemented for %s" % (repr(obj)))
            return serFn(obj)
    
    @classmethod
    def FunctionContainer(cls, fn):
        raise NotImplementedError()
        # return fn.name
    @classmethod
    def Entity(cls, ent, scope):
        return ""
        
    @classmethod
    def Architecture(cls, arch, scope):
        variables = []
        procs = []
        extraTypes = set()
        extraTypes_serialized = []
        arch.variables.sort(key=lambda x: x.name)
        arch.processes.sort(key=lambda x: (x.name, maxStmId(x)))
        arch.componentInstances.sort(key=lambda x: x._name)
        
        for v in arch.variables:
            t = v._dtype
            # if type requires extra definition
            if isinstance(t, (Enum, Array)) and t not in extraTypes:
                extraTypes.add(v._dtype)
                extraTypes_serialized.append(cls.HdlType(t, scope, declaration=True))

            v.name = scope.checkedName(v.name, v)
            variables.append(v)
            
        
        for p in arch.processes:
            procs.append(cls.HWProcess(p, scope, 0))
        
        # architecture names can be same for different entities
        # arch.name = scope.checkedName(arch.name, arch, isGlobal=True)    
             
        return unitTmpl.render({
        "name"               : arch.getEntityName(),
        "ports"              : list(map(lambda p: (p.name, cls.HdlType(p._dtype)), arch.entity.ports)),
        "signals"            : list(map(lambda v: (v.name, cls.HdlType(v._dtype), cls.Value(evalParam(v.defaultVal))), variables)),
        "extraTypes"         : extraTypes_serialized,
        "processes"          : procs,
        "processObjects"     : arch.processes,
        "processesNames"     : map(lambda p: p.name, arch.processes),
        "componentInstances" : arch.componentInstances
        })
   
    @classmethod
    def Assignment(cls, a):
        dst = a.dst
        if dst._dtype == a.src._dtype:
            if a.indexes is not None:
                raise NotImplementedError()
            else:
                return "yield (self.%s, mkUpdater(%s), %r)" % (dst.name, cls.Value(a.src), a.isEventDependent)
        else:
            raise SerializerException("%s <= %s  is not valid assignment\n because types are different (%s; %s) " % 
                         (cls.asHdl(dst), cls.Value(a.src), repr(dst._dtype), repr(a.src._dtype)))
        
    @classmethod
    def comment(cls, comentStr):
        return "#" + comentStr.replace("\n", "\n#")     

    @classmethod
    def condAsHdl(cls, cond, forceBool):
        if isinstance(cond, RtlSignalBase):
            cond = [cond]
        else:
            cond = list(cond)
        if len(cond) == 1:
            c = cond[0]
            if not forceBool or c._dtype == BOOL:
                return cls.asHdl(c)
            elif c._dtype == BIT:
                return "(" + cls.asHdl(c) + ")=" + cls.BitLiteral(1, 1) 
            elif isinstance(c._dtype, Bits):
                width = c._dtype.bit_length()
                return "(" + cls.asHdl(c) + ")/=" + cls.BitString(0, width)
            else:
                raise NotImplementedError()
            
        else:
            return " AND ".join(map(lambda x: cls.condAsHdl(x, forceBool), cond))
    
    @classmethod
    def IfContainer(cls, ifc):
        cond = cls.condAsHdl(ifc.cond, True)
        elIfs = []
        ifTrue = ifc.ifTrue
        ifFalse = ifc.ifFalse
        
        for c, statements in ifc.elIfs:
                
            elIfs.append((cls.condAsHdl(c, True), statements))
        
        return VHDLTemplates.If.render(cond=cond,
                                       ifTrue=ifTrue,
                                       elIfs=elIfs,
                                       ifFalse=ifFalse)  
    
    @classmethod
    def SwitchContainer(cls, sw):
        switchOn = cls.condAsHdl(sw.switchOn, False)
        
        cases = []
        for key, statements in sw.cases:
            if key is not None:  # None is default
                key = cls.asHdl(key)
                
            cases.append((key, statements))  
        return VHDLTemplates.Switch.render(switchOn=switchOn,
                                           cases=cases)  
   
    @classmethod
    def WaitStm(cls, w):
        if w.isTimeWait:
            return "wait for %d ns" % w.waitForWhat
        elif w.waitForWhat is None:
            return "wait"
        else:
            raise NotImplementedError()
        
    @staticmethod
    def BitString_binary(v, width, vldMask=None):
        buff = []
        for i in range(width - 1, -1, -1):
            mask = (1 << i)
            b = v & mask
            
            if vldMask & mask:
                s = "1" if b else "0"
            else:
                s = "X"
            buff.append(s)
        return '"%s"' % (''.join(buff))

    @classmethod
    def BitString(cls, v, width, vldMask=None):
        if vldMask is None:
            vldMask = Bitmask.mask(width)
        # if can be in hex
        if width % 4 == 0 and vldMask == (1 << width) - 1:
            return ('X"%0' + str(width // 4) + 'x"') % (v)
        else:  # else in binary
            return cls.BitString_binary(v, width, vldMask)
    
    @classmethod
    def BitLiteral(cls, v, vldMask):
        if vldMask:
            return  "'%d'" % int(bool(v))
        else:
            return "'X'"
    
    @classmethod
    def SignedBitString(cls, v, width, vldMask):
        if vldMask != Bitmask.mask(width):
            raise SerializerException(
            "Value %s can not be serialized as signed bit string literal due not all bits are valid" % 
             repr(v))
        else:
            # [TODO] parametrized width
            return "TO_SIGNED(%d, %d)" % (v, width)

    @classmethod
    def UnsignedBitString(cls, v, width, vldMask):
        if vldMask != Bitmask.mask(width):
            raise SerializerException(
            "Value %s can not be serialized as signed bit string literal due not all bits are valid" % 
             repr(v))
        else:
            # [TODO] parametrized width
            return "TO_UNSIGNED(%d, %d)" % (v, width)
    
    @classmethod
    def SignalItem(cls, si, declaration=False):
        if declaration:
            if si.drivers:
                prefix = "SIGNAL"
            elif si.endpoints or si.simSensitiveProcesses:
                prefix = "CONSTANT"
            else:
                raise SerializerException("Signal %s should be declared but it is not used" % si.name)
                

            s = prefix + " %s : %s" % (si.name, cls.HdlType(si._dtype))
            if si.defaultVal is not None:
                v = si.defaultVal
                if isinstance(v, RtlSignalBase):
                    return s + " := %s" % cls.asHdl(v)
                elif isinstance(v, Value):
                    if si.defaultVal.vldMask:
                        return s + " := %s" % cls.Value(si.defaultVal)
                else:
                    raise NotImplementedError(v)
                
            return s 
        else:
            if si.hidden and hasattr(si, "origin"):
                return cls.asHdl(si.origin)
            else:
                return "self.%s._oldVal" % si.name

    @classmethod
    def HdlType_bits(cls, typ, declaration=False):
        disableRange = False
        if typ.signed is None:
            if not (typ.forceVector or typ.bit_length() > 1):
                return 'BIT'
            
        c = typ.constrain
        if isinstance(c, (int, float)):
            pass
        else:        
            c = evalParam(c)
            if isinstance(c, SliceVal):
                c = c._size()
            else:
                c = c.val  
             
        return "vecT(%d, %r)" % (c, typ.signed)

    @classmethod
    def HdlType_enum(cls, typ, scope, declaration=False):
        buff = []
        if declaration:
            try:
                name = typ.name
            except AttributeError:
                name = "enumT_"
            typ.name = scope.checkedName(name, typ)
            
            buff.extend(["TYPE ", typ.name.upper(), ' IS ('])
            # [TODO] check enum values names 
            buff.append(", ".join(typ._allValues))
            buff.append(")")
            return "".join(buff)
        else:
            return typ.name
        

    @classmethod
    def HdlType_array(cls, typ, scope, declaration=False):
        if declaration:
            try:
                name = typ.name
            except AttributeError:
                name = "arrT_"
            
            typ.name = scope.checkedName(name, typ)
            
            return "TYPE %s IS ARRAY ((%s) DOWNTO 0) OF %s" % \
                (typ.name, cls.asHdl(toHVal(typ.size) - 1), cls.HdlType(typ.elmType))
        else:
            try:
                return typ.name
            except AttributeError:
                # [TODO]
                # sometimes we need to debug expression and we need temporary type name
                # this may be risk and this should be done by extra debug serializer
                return "arrT_%d" % id(typ) 

    @classmethod
    def HdlType(cls, typ, scope=None, declaration=False):
        assert isinstance(typ, HdlType)
        if isinstance(typ, Bits):
            return cls.HdlType_bits(typ, declaration=declaration)
        elif isinstance(typ, Enum):
            return cls.HdlType_enum(typ, scope, declaration=declaration)
        elif isinstance(typ, Array):
            return cls.HdlType_array(typ, scope, declaration=declaration)
        else:
            if declaration:
                raise NotImplementedError("type declaration is not implemented for type %s" % 
                                      (typ.name))
            else:
                return typ.name.upper()
                
    @classmethod
    def HWProcess(cls, proc, scope, indentLvl):
        body = proc.statements
        proc.name = scope.checkedName(proc.name, proc)
        
        
        sensitivityList = sorted(where(proc.sensitivityList,
                                       lambda x : not isinstance(x, Param)), key=lambda x: x.name)
        
        return processTmpl.render({
              "name": proc.name,
              "sensitivityList": ", ".join([s.name for s in sensitivityList]),
              "stmLines": [ cls.asHdl(s) for s in body] })
           
    @classmethod
    def BitToBool(cls, cast):
        v = 0 if cast.sig.negated else 1
        return cls.asHdl(cast.sig) + "._eq(hBit(%d))" % v

    @classmethod
    def Operator(cls, op):
        def p(operand):
            s = cls.asHdl(operand)
            if isinstance(operand, RtlSignalBase):
                try:
                    o = operand.singleDriver()
                    if opPrecedence[o.operator] <= opPrecedence[op.operator]:
                        return " (%s) " % s
                except Exception:
                    pass
            return " %s " % s
        
        ops = op.ops
        o = op.operator
        def _bin(name):
            return (" " + name + " ").join(map(lambda x: x.strip(), map(p, ops)))
        
        if o == AllOps.AND_LOG:
            return _bin('&')
        elif o == AllOps.OR_LOG:
            return _bin('|')
        elif o == AllOps.XOR:
            return _bin('^')
        elif o == AllOps.NOT:
            assert len(ops) == 1
            return "~" + p(ops[0])
        elif o == AllOps.CALL:
            return "%s(%s)" % (cls.FunctionContainer(ops[0]), ", ".join(map(p, ops[1:])))
        elif o == AllOps.CONCAT:
            return "Concat(%s, %s)" % (p(ops[0]), p(ops[1]))
        elif o == AllOps.DIV:
            return _bin('//')
        elif o == AllOps.DOWNTO:
            return _bin(':')
        elif o == AllOps.EQ:
            return '(%s)._eq(%s)' % (p(ops[0]), p(ops[1]))
        elif o == AllOps.EVENT:
            assert len(ops) == 1
            return p(ops[0]) + "._hasEvent(sim)"
        elif o == AllOps.GREATERTHAN:
            return _bin('>')
        elif o == AllOps.GE:
            return _bin('>=')
        elif o == AllOps.LE:
            return _bin('<=')
        elif o == AllOps.INDEX:
            assert len(ops) == 2
            return "%s[%s]" % ((cls.asHdl(ops[0])).strip(), p(ops[1]))
        elif o == AllOps.LOWERTHAN:
            return _bin('<')
        elif o == AllOps.SUB:
            return _bin('-')
        elif o == AllOps.MUL:
            return _bin('*')
        elif o == AllOps.NEQ:
            return _bin('!=')
        elif o == AllOps.ADD:
            return _bin('+')
        elif o == AllOps.TERNARY:
            return p(ops[1]) + " if " + cls.condAsHdl([ops[0]], True) + " else " + p(ops[2])
        #elif o == AllOps.RISING_EDGE:
        #    assert len(ops) == 1
        #    return "RISING_EDGE(" + p(ops[0]) + ")"
        #elif o == AllOps.FALLIGN_EDGE:
        #    assert len(ops) == 1
        #    return "FALLING_EDGE(" + p(ops[0]) + ")"
        elif o == AllOps.BitsAsSigned:
            assert len(ops) == 1
            return  "%s._signed()" % p(ops[0])
        elif o == AllOps.BitsAsUnsigned:
            assert len(ops) == 1
            return  "%s._unsigned()" % p(ops[0])
        elif o == AllOps.BitsAsVec:
            assert len(ops) == 1
            return  "%s._vec()" % p(ops[0])
        # elif o == AllOps.BitsToInt:
        #    assert len(ops) == 1
        #    op = cls.asHdl(ops[0])
        #    if ops[0]._dtype.signed is None:
        #        op = "UNSIGNED(%s)" % op
        #    return "TO_INTEGER(%s)" % op
        # elif o == AllOps.IntToBits:
        #    assert len(ops) == 1
        #    resT = op.result._dtype
        #    op_str = cls.asHdl(ops[0])
        #    w = resT.bit_length()
        #    
        #    if resT.signed is None:
        #        return "STD_LOGIC_VECTOR(TO_UNSIGNED(" + op_str + ", %d))" % (w)
        #    elif resT.signed:
        #        return "TO_UNSIGNED(" + op_str + ", %d)" % (w)
        #    else:
        #        return "TO_UNSIGNED(" + op_str + ", %d)" % (w)
        #    
        elif o == AllOps.POW:
            assert len(ops) == 2
            return  "pow(%s, %s)" % (p(ops[0]), p(ops[1]))
        else:
            raise NotImplementedError("Do not know how to convert %s to vhdl" % (o))
