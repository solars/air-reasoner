"""tms.py

A zeroth order attempt at building a TMS



see http://dig.csail.mit.edu/TAMI/2007/amord/tms.scm
"""

import Queue

from py25 import *


class TMS(object):
    """A (singleton) class used to control how signalling and
    contradictions are handled within the nodes in the TMS."""
    def __init__(self, name, event_handler):
        self.name = name
        self.event_handler = event_handler
        self.contradiction = Node(self, "contradiction")

class Node(object):
    """A node in a TMS tree.  Usually has an associated datum (if it is an
    assertion, for example), as well as pointers to those things which it
    uses as support and those things that are consequents thereof."""
    nodeCount = 0
    
    def __init__(self, tms, datum=None):
        self.__class__.nodeCount += 1
        self.count = self.nodeCount
        self.tms = tms
        self.datum = datum
        self.supported = False
        self.justifications = set()
        self.consequents = set()
        # Hacks to make the new justification ontology work.
        self.extractedFrom = None
        self.dataEvent = None
        self.assumedURIs = []
        self.fireEvent = None
        self.isBuiltIn = False
        self.parsedFrom = None

    def justify(self, rule, expression, hypotheses=None):
        if hypotheses is None:
            hypotheses = set()
        if isinstance(expression, (set, frozenset, list, tuple)):
            expression = AndExpression(list(expression))
        c = ExpressionJustification(self, rule, expression, hypotheses)
        return None

    @property
    def name(self):
        return 'n%s = %s' % (self.count, self.datum)

    def __repr__(self):
        return self.name

    def signal(self, justification):
        (self.tms.event_handler)(self, justification)

    def isContradicted(self):
        return self is self.tms.contradiction

    def premise(self):
        """Return the premise of this node, if any."""
        try:
            return [x for x in self.justifications if isinstance(x, Premise)][0]
        except IndexError:
            return None

    def assumed(self):
        """Return true if this node has been assumed (it has a premise which
        is supported???)"""
        just = self.premise()
        if just is None:
            return False
        return just.supported

    def assume(self):
        """Assume this node by marking its premise as supported (or using
        itself as a premise)"""
        just = self.premise()
        if just is not None:
            just.supported = True
        else:
            just = Premise(self)
        self.support(just)
        
    # The following four are for the new ontology working.
    def assumeByExtraction(self, uri):
        """Assume this node and mark the URI from which the particular
        assumption is based."""
        # Like assume(), but adds the fact that it was extracted from uri.
        self.assume()
        self.setExtractedFrom(uri)
    
    def setExtractedFrom(self, uri):
        """Set the URI from which this node was extracted."""
        # Sets the extractedFrom value recursively.
        self.extractedFrom = uri
        for just in self.consequents:
            just.consequent.setExtractedFrom(uri)
            
    def assumeByClosingWorld(self, assumedPolicies, assumedURIs, assumedStrings,
                             assumedCWAs):
        """Assume this node in a closing the world operation by marking the
        URIs currently being assumed in this closed world."""
        # Like assume(), but adds the assumedURIs. (Anything else???)
        self.assume()
        self.setAssumedURIs(assumedURIs)
    
    def setAssumedURIs(self, assumedURIs):
        """Sets the URIs currently being assumed."""
        self.assumedURIs = assumedURIs
    
    def assumeBuiltin(self):
        """Assume this node by virtue of its being a built-in."""
        self.assume()
        self.isBuiltIn = True
    
    def assumeByParsingN3(self, formula):
        """Assume this node by virtue of parsing a given formula from N3."""
        self.assume()
        self.setParsedFrom(formula)

    def setParsedFrom(self, formula):
        """Set the formula from which this node was parsed."""
        # Sets the parsedFrom value recursively.
        self.parsedFrom = formula
        for just in self.consequents:
            just.consequent.setParsedFrom(formula)
            
    def support(self, justification):
        """Support this TMS node and any consequents."""
        if self.supported:
            return
        self.supported = True
        self.signal(justification)
        # Recursively support any consequents as long as their support is well-founded.
        for just in self.consequents:
            if just.supported:
                just.consequent.support(just)
            else:
                if just.consequent.supported and not just.consequent.hasWellFoundedSupport():
                    just.consequent.supported = False
                    just.consequent.signal(False)
                    reSupportNodes(just.consequent.propagateRetractions())

    def retract(self):
        """Retract the assumption of a TMS node."""
        premise = self.premise()
        if premise is None:
            raise RetractionError('Cannot retract a node that was not assumed in the first place, %s' % self)
        premise.supported = False
        if self.supported:
            self.supported = False
            self.signal(False)
            reSupportNodes(self.propagateRetractions())
        for just in self.consequents:
            if just.supported:
                just.consequent.support(just)

    def propagateRetractions(self):
        """Generate the consequents of this particular node recursively."""
        for just in self.consequents:
            if not just.supported:
                for x in just.propagateRetractions():
                    yield x

    def reSupport(self):
        if self.supported:
            return
        self.supported = True
        for just in self.consequents:
            if just.supported:
                just.consequent.reSupport()

    def hasWellFoundedSupport(self, seen=set(), hypotheses=None):
        if hypotheses is None:
            hypotheses = set()
        if self in seen:
            return False
        elif self in hypotheses:
            return True
        else:
            return any(just.hasWellFoundedSupport(seen.union(set([self])),
                                                                              hypotheses)
                                   for just in self.justifications)


    def evaluate(self, node_value):
        return node_value(self)


    def supportingAssumptions(self):
        tree = self.supportTree()
        if tree:
            return tree[2]
        return False

    def supportTree(self):
        """Return the tree of support for this node (see walkNode)"""

        def walkNode(node, seen, hypotheses):
            if node in seen:
                return False
            seen = seen.union([node])
            jTrees = []
            support = set()
            for just in node.justifications:
                jTree = walkJust(just, node, seen, hypotheses)
                if jTree:
                    jTrees.append(jTree)
                    support.update(jTree[2])
            if not jTrees:
                return False
            return (node, jTrees, support)

        def walkJust(just, node, seen, hypotheses):
            if isinstance(just, Premise):
                if just.supported or just in hypotheses:
                    return (just, [], set([node]))
                else:
                    return False
            else:
                hypotheses = just.hypotheses.union(hypotheses)
                node_value = Memoizer(lambda node: walkNode(node, seen, hypotheses))
                if not just.expression.evaluate(node_value): # changes dictionary in memoizer
                    return False
                nTrees = []
                for key, val in sorted(node_value.vals.items()):
                    if val is False:
                        nTrees.append((key, [], set()))
                    else:
                        nTrees.append(val)
                return (just, nTrees, reduce(set.union, [x[2] for x in nTrees], set()))

                
##                nTrees = []
##                support = set()
##                for node in just.antecedents:
##                    nTree = walkNode(node, seen, hypotheses)
##                    if nTree:
##                        nTrees.append(nTree)
##                        support.update(nTree[2])
##                    else:
##                        return False # I don't know if this is right
##                return (just, nTrees, support.difference(hypotheses))
            
        return walkNode(self, set(), set())

    ##### printing stuff

    def why(self, port=None):
        """Print the justification for this node."""
        def f(consequent, just, antecedents, support): 
            writeComment("""(%s) <== (%s %s) | support = %s """ % (consequent.name,
                                                                                  just.rule or just,
                                                                                  ' & '.join(['(%s)' % x.name for x in antecedents]),
                                                                                  support),
                                     port)
        self.walkSupport(f)

    def walkSupport(self, procedure):
        """Walk the support tree (breadth-first), executing procedure on each
        node."""
        nTree = self.supportTree()
        assert nTree, 'Bad range argument to %s.walkSupport(), got back %s' % (self, nTree)
        queue = Queue.Queue()  #should I use this? (overkill)
        seen = set()
        def maybeEnqueue(nt):
            if nt[0] not in seen:
                queue.put(nt)
                seen.add(nt[0])
        maybeEnqueue(nTree)
        while not queue.empty():
            nt = queue.get()
            jTrees = nt[1]
            if jTrees:
                jTree = iter(jTrees).next()  # I don't remember if this is needed
                procedure(nt[0],
                                 jTree[0],
                                 [x[0] for x in jTree[1]],
                                 jTree[2])
                for x in jTree[1]:
                    maybeEnqueue(x)

import sys
def writeComment(obj, port=None):
    """Writes a comment."""
    if port is None:
        port = sys.stdout
        port.write('# %s \n' % obj)


def reSupportNodes(retracted):
    """Sends a false (retraction) signal to any nodes in retracted which
    are no longer (recursively) supported by justifications marked as
    such."""
    newSupport = True
    while newSupport:
        newSupport = False
        nodes = retracted
        retracted = []
        for node in nodes:
            if node.supported:
                pass
            elif any(just.supported for just in node.justifications):
                newSupport = True
                node.reSupport()
            else:
                retracted.append(node)
    for node in retracted:
        node.signal(False)


###
#  ===== Boolean  expressions =====
###

class BooleanExpression(object):
    NOT = 0
    AND = 1
    OR = 2
    def __init__(self, op, args):
        if op == self.NOT:
            assert len(args) == 1
        self.op = op
        self.args = args

    def evaluate(self, node_value):
        raise NotImplementedError

    def nodes(self):
        self.nodes = NotImplemented
        v = set()
        for val in self.args:
            if isinstance(val, BooleanExpression):
                v.update(val.nodes())
            else:
                v.add(val)
        v = frozenset(v)
        self.nodes = lambda: v
        return v
        

    def __repr__(self):
        return '%s(%s)' % ({self.NOT:"NOT",
                                        self.AND:"AND",
                                        self.OR: "OR"}[self.op],
                           ','.join([repr(x) for x in self.args]))

class NotExpression(BooleanExpression):
    def __init__(self, arg):
        BooleanExpression.__init__(self, self.NOT, [arg])
    def evaluate(self, node_value):
        return not self.args[0].evaluate(node_value)

class OrExpression(BooleanExpression):
    def __init__(self, args=[]):
        new_args = []
        for arg in args:
            if isinstance(arg, OrExpression):
                new_args.extend(arg.args)
            else:
                new_args.append(arg)
        BooleanExpression.__init__(self, self.OR, new_args)
    def evaluate(self, node_value):
        return any(x.evaluate(node_value) for x in self.args)

class AndExpression(BooleanExpression):
    def __init__(self, args=[]):
        new_args = []
        for arg in args:
            if isinstance(arg, AndExpression):
                new_args.extend(arg.args)
            else:
                new_args.append(arg)
        BooleanExpression.__init__(self, self.AND, new_args)
    def evaluate(self, node_value):
        return all(x.evaluate(node_value) for x in self.args)


###
##  ====== Justifications =============
###


class Memoizer(object):
    def __init__(self, f):
        self.f = f
        self.vals = {}

    def __call__(self, arg): # we only support one argument for now
        try:
            return self.vals[arg]
        except KeyError:
            v = self.f(arg)
            self.vals[arg] = v
            return v

class Justification(object):
    def __init__(self, consequent):
        self._consequent = consequent

    @property
    def consequent(self):
        return self._consequent

    def attach(self):
        node = self.consequent
        for node2 in self.expression.nodes():
            node2.consequents.add(self)
        node.justifications.add(self)
        if self.supported:
            node.support(self)

    def propagateRetractions(self):
        node = self.consequent
        if node.supported:
            node.supported = False
            yield node
            for x in node.propagateRetractions():
                yield x

    def hasWellFoundedSupport(self, seen, hypotheses):
        new_hyp = hypotheses.union(self.hypotheses)
        return self.evaluate(lambda node: node.hasWellFoundedSupport(seen, hypotheses))

    def evaluate(self, node_value):
        node_value = Memoizer(node_value)
        return self.expression.evaluate(node_value)
        
        
class Premise(Justification):
    def __init__(self, consequent):
        Justification.__init__(self, consequent)
        self.supported = True
        self.attach()

    @property
    def rule(self):
        return "Premise"
    @property
    def expression(self):
        return AndExpression()
    @property
    def hypotheses(self):
        return set()

    def hasWellFoundedSupport(self, seen, hypotheses):
        return self.supported


class ExpressionJustification(Justification):
    def __init__(self, consequent, rule, expression, hypotheses=None):
        Justification.__init__(self, consequent)
        assert not isinstance(rule, (str, unicode)), rule
        self.rule = rule
        self.expression = expression
        if hypotheses is None:
            hypotheses = set()
        self.hypotheses = hypotheses
        self.attach()

    def __repr__(self):
        return '%s(%s,%s,%s, %s)' % ('ExpressionJustification', self.consequent,
                                                                           self.rule,
                                                                           self.expression,
                                                                           self.hypotheses)

    @property
    def supported(self):
        if all([x.supported for x in self.hypotheses]):
            return self.evaluate(lambda x: x.supported)
        return hasWellFoundedSupports(self)

test = """

>>> import tms
>>> Tms = tms.TMS('n', lambda node, just: tms.writeComment('%s %s' % (just and 'supported' or 'unsupported', node.name)))
>>> a = tms.Node(Tms, 'A')
>>> b = tms.Node(Tms, 'B')
>>> c = tms.Node(Tms, 'C')
>>> d = tms.Node(Tms, 'D')
>>> e = tms.Node(Tms, 'E')
>>> f = tms.Node(Tms, 'F')
>>> g = tms.Node(Tms, 'G')
>>> h = tms.Node(Tms, 'H')
>>> i = tms.Node(Tms, 'I')
>>> e.justify('MP', [a,b])
>>> e.justify('X', [c])
>>> g.justify('Y', [e])
>>> h.justify('Z', [e,f])
>>> f.justify('W', [c,d])
>>> c.assume()
# supported n4 = C 
# supported n6 = E 
# supported n8 = G 
>>> c.supported
True
>>> e.supported
True
>>> g.why()
# (n8 = G) <== (Y (n6 = E)) | support = set([n4 = C])  
# (n6 = E) <== (X (n4 = C)) | support = set([n4 = C])  
# (n4 = C) <== (Premise ) | support = set([n4 = C])  
>>> c.retract()
# unsupported n4 = C 
# unsupported n6 = E 
# unsupported n8 = G 
>>> a.assume()
# supported n2 = A 
>>> b.assume()
# supported n3 = B 
# supported n6 = E 
# supported n8 = G 
>>> c.assume()
# supported n4 = C 
>>> d.assume()
# supported n5 = D 
# supported n7 = F 
# supported n9 = H 
>>> a.retract()
# unsupported n2 = A 
>>> h.why()
# (n9 = H) <== (Z (n6 = E) & (n7 = F)) | support = set([n4 = C, n5 = D])  
# (n6 = E) <== (X (n4 = C)) | support = set([n4 = C])  
# (n7 = F) <== (W (n4 = C) & (n5 = D)) | support = set([n4 = C, n5 = D])  
# (n4 = C) <== (Premise ) | support = set([n4 = C])  
# (n5 = D) <== (Premise ) | support = set([n5 = D])  
>>> c.retract()
# unsupported n4 = C 
# unsupported n6 = E 
# unsupported n8 = G 
# unsupported n9 = H 
# unsupported n7 = F 
>>> c.assume()
# supported n4 = C 
# supported n6 = E 
# supported n8 = G 
# supported n7 = F 
# supported n9 = H 
>>> d.retract()
# unsupported n5 = D 
# unsupported n7 = F 
# unsupported n9 = H 
>>> i.justify('U', [f])
>>> i.justify('Q', [g])
# supported n10 = I 
>>> d.justify('R', [i])
# supported n5 = D 
# supported n7 = F 
# supported n9 = H 
>>> h.why()
# (n9 = H) <== (Z (n6 = E) & (n7 = F)) | support = set([n4 = C])  
# (n6 = E) <== (X (n4 = C)) | support = set([n4 = C])  
# (n7 = F) <== (W (n4 = C) & (n5 = D)) | support = set([n4 = C])  
# (n4 = C) <== (Premise ) | support = set([n4 = C])  
# (n5 = D) <== (R (n10 = I)) | support = set([n4 = C])  
# (n10 = I) <== (Q (n8 = G)) | support = set([n4 = C])  
# (n8 = G) <== (Y (n6 = E)) | support = set([n4 = C])  
>>> c.retract()
# unsupported n4 = C 
# unsupported n6 = E 
# unsupported n8 = G 
# unsupported n9 = H 
# unsupported n7 = F 
# unsupported n10 = I 
# unsupported n5 = D 
>>> a.assume()
# supported n2 = A 
# supported n6 = E 
# supported n8 = G 
# supported n10 = I 
# supported n5 = D 
>>> d.why()
# (n5 = D) <== (R (n10 = I)) | support = set([n3 = B, n2 = A])  
# (n10 = I) <== (Q (n8 = G)) | support = set([n3 = B, n2 = A])  
# (n8 = G) <== (Y (n6 = E)) | support = set([n3 = B, n2 = A])  
# (n6 = E) <== (MP (n3 = B) & (n2 = A)) | support = set([n3 = B, n2 = A])  
# (n3 = B) <== (Premise ) | support = set([n3 = B])  
# (n2 = A) <== (Premise ) | support = set([n2 = A])  
>>> c.assume()
# supported n4 = C 
# supported n7 = F 
# supported n9 = H 
>>> c.retract()
# unsupported n4 = C 
# unsupported n7 = F 
# unsupported n9 = H 



>>> aa = tms.Node(Tms, 'a')
>>> bb = tms.Node(Tms, 'b')
>>> aa.justify('B', tms.NotExpression(bb))
# supported n11 = a 
>>> bb.assume()
# supported n12 = b 
# unsupported n11 = a 
>>> bb.retract()
# unsupported n12 = b 
# supported n11 = a 
>>> bb.assume()
# supported n12 = b 
# unsupported n11 = a 

"""

__test__ = {'tms' : test}
    
if __name__ == '__main__':
    import doctest
    doctest.testmod()
