"""Rete.py

a rete implementation independant of pychinko, that uses IndexedFormula


This is based heavily on RETE-UL from
   http://reports-archive.adm.cs.cmu.edu/anon/1995/CMU-CS-95-113.pdf
Some inspiration for this came from pychinko; though not enough


"""

import weakref
WKD = weakref.WeakKeyDictionary
from collections import deque
import itertools

from term import unify, Env, BuiltIn, Function, ReverseFunction, MultipleFunction, MultipleReverseFunction, ListBuiltIn, listify, isNonEmptyListTerm, Fragment
from formula import Formula, StoredStatement, WME
from query import think as cwmThink

import operator

from py25 import dequeRemove
VAR_PLACEHOLDER = object()

Air_NS = 'http://dig.csail.mit.edu/TAMI/2007/amord/air#'

fullUnify = False

OWL_RULES = 'http://dig.csail.mit.edu/TAMI/2007/amord/owl-rules.n3'

def compilePattern(index, patterns, vars, context, buildGoals=False,
                   goalBottomBeta=None, supportBuiltin=None,
                   ignoreBuiltins=False, reachedGoal=lambda: False):
    """Compile the RETE network for a given set of patterns and return the
    'goal' node."""
    if buildGoals:
        # Root node is determined by the matched goals.
        current = EmptyRoot
    elif goalBottomBeta is not None:
        # The root node comes from a previously compiled goal pattern.
        current = EmptyRoot
        # TODO: But the variable bindings should be passed in.
    else:
        # Start with an empty root node...
        current = EmptyRoot
    for pattern in sortPatterns(patterns, vars, ignoreBuiltins):
        # And for each pattern in order, build the alpha filter, join
        # node, and beta memory.
        alpha = AlphaFilter.build(index, pattern, vars, context,
                                  supportBuiltin=supportBuiltin,
                                  reachedGoal=reachedGoal)
        current = JoinNode(current, alpha, buildGoals, reachedGoal=reachedGoal)
        current = BetaMemory(current, reachedGoal=reachedGoal)

    # The last pattern matched is, of course, the one that triggers
    # any success for the RETE match (and the only one that matters).
    return current

def compileGoalPattern(index, patterns, vars, 
                       goalWildcards, goalContext, goalPatterns,
                       supportBuiltin=None):
    """Compile the RETE network for a given set of goal patterns and return the
    'goal' node."""
    # Goal-direction only ever matches one pattern at a time, so the
    # construction is effectively a giant OR.
    alphas = [GoalAlphaFilter.build(index, pattern, vars, goalContext,
                                    goalWildcards,
                                    supportBuiltin=supportBuiltin)
              for pattern in goalPatterns]

    # TODO: Alphas should match goalWildcard in goalContext
    # (Non-binding variable)
    # Join all alphas simultaneously (giant OR)
    output = GoalJoinNode(EmptyRoot, alphas)
    output = BetaMemory(output)

    # The last pattern matched is, of course, the one that triggers
    # any success for the RETE match (and the only one that matters).
    return output

    
#### Dealing with builtins
## We need code to do a sort of sort here.
## A naive topological sort may not work
## Triples will rely on variables only --- but we are sorting triples, not variables

class CyclicError(ValueError):
    """Thrown when a cyclic dependency is found."""
    pass

def sortPatterns(patterns, vars, ignoreBuiltins=False):
    """Sort the given patterns topologically, such that built-ins are
    satisfied as late as possible, but maintaining the order of
    built-ins so that any bindings required by a built-in are met
    before it is matched."""

    # We don't care about general patterns, really (all triples that
    # will match should already be present).  We just care that the
    # built-ins are at the end, as those triples aren't actually
    # present, and hence, we need to make sure that all arguments are
    # matched PRIOR to the builtins running.
    
    # There's a particular corner case: if something is both a
    # Function and a ReverseFunction, by default, it will never be
    # satisfied.  This isn't a problem though, if we remember this:
    # 
    # The function will be forced into one of those two roles when
    # there are no variables left that can be bound more easily
    # (e.g. with built-ins that are exclusively functions or reverse
    # functions), and at least ONE side is fully bound and the other
    # has at most one variable left unbound.  At that point, we can
    # determine what role the built-in needs to be in to fully satisfy
    # the Rete, and we process it as that role and continue to sort
    # the remaining patterns until nothing's left.
    #
    # TODO: Still a corner case...  What if there's a cycle? A
    # MultipleFunction?

    vars = set(vars)
    requires = {}
    provides = {}
    unresolvables = set()
    patterns.sort()
    for pattern in patterns:
        # Each pattern should be keeping track of its requires and its
        # provides.  Special case, however, if a pattern is of unknown
        # type (Function or ReverseFunction)...  We don't know
        # requires or provides at this time, and it's currently
        # unresolvable.
        if pattern.predicate() != pattern.predicate().store.sameAs:
            # c.f. StoredStatement.requires() in formula.py
            for var in pattern.requires(vars):
                requires.setdefault(var, set()).add(pattern)
        # c.f. StoredStatement.provides() in formula.py
        for var in pattern.provides(vars):
            provides.setdefault(var, set()).add(pattern)
        # c.f. StoredStatement.isUnresolvable() in formula.py
        if pattern.isUnresolvable(vars):
            unresolvables.add(pattern)

    def getTopologically():
        """Sort patterns topologically by requires, provides, and
        indeterminates."""
        
        # Nodes in the graph are our patterns.
        nodes = patterns
        inDegrees = {}
        for node in nodes:
            inDegrees[node] = len(node.requires(vars))
            
            # Not sure what's going on with owl:sameAs.  It's a
            # BuiltIn and rule?  Gotta ignore CyclicErrors raised as a
            # result of it.
            if node.predicate() == node.predicate().store.sameAs:
                inDegrees[node] = 0
        zeros = deque()
        for node in nodes:
            if inDegrees[node] == 0:
                zeros.appendleft(node)
        provided = set()
        while zeros:
            top = zeros.pop()
            yield top
            for var in top.provides(vars, provided):
                if var in provided:
                    continue
                else:
                    for node in requires.get(var, []):
                        inDegrees[node] = inDegrees[node] - 1
                        if inDegrees[node] == 0:
                            zeros.appendleft(node)
                    provided.add(var)
            if not zeros:
                # Well, we're out of things we can resolve directly.
                # Let's see if we can force a previously unresolvable
                # pattern to resolve now (i.e. if we now know if
                # whether it should behave as a Function or a
                # ReverseFunction based on now-provided variables.)
                
                popnodes = set()
                for node in unresolvables:
                    if not node.isUnresolvable(vars, provided):
                        # It's resolvable, which implies that we have,
                        # in fact, met the inDegrees=0 requirement,
                        # even though it's not evident.
                        inDegrees[node] = 0
                        for var in requires.keys():
                            if node in requires[var]:
                                requires[var].remove(node)
                        zeros.appendleft(node)
                        popnodes.add(node)
                for node in popnodes:
                    unresolvables.remove(node)
        
        if len(inDegrees) != 0 and max(inDegrees.values()) != 0:
            raise CyclicError, "You've got a cyclic dependency in your rule, buddy!"
    
    if ignoreBuiltins:
        return list(patterns)
    else:
        return list(getTopologically())

### end builtins

class BogusTripleError(RuntimeError):
    pass

class BogusTriple(StoredStatement):  
    def __init__(self, triple):
        raise BogusTripleError('The building of BogusTriples should have been stopped')
        if hasattr(triple, 'quad'):
            triple = triple.quad
        StoredStatement.__init__(self, triple)

    def __repr__(self):
        return 'BogusTriple(%s)' % (self.quad,)


#WMEData = WKD()



def removeStatement(s):
    W = s.WME
    for item in W.alphaMemItems:
        item.remove(s)
        if not item:
            item.empty = True
            for node in item.successors:
                if isinstance(node, JoinNode):
                    node.parent.children.remove(node)
        while W.tokens:
            t = W.tokens.pop()
            t.delete()



class TripleWithBinding(object):
    """A (triple, binding) pair to pass from an alpha node to a beta node
"""
    def __init__(self, triple, env):
        self.triple = triple
        self.env = env
        self.WME = WME()

    def __eq__(self, other):
        if isinstance(other, TripleWithBinding):
            return self.triple == other.triple and self.env == other.env
        return self.triple == other


    def __repr__(self):
        return '%s(%s,%s)' % ('TWB', self.triple, self.env)


class AlphaMemory(list):
    """An alpha memory node matched triples against one triple of the pattern.
This base class only knows variables, and is part of the IndexedFormula.
"""
    def __init__(self):
        self.successors = deque()
        self.empty = True
        list.__init__(self)

    def add(self, s):
        self.append(s)
        W = s.WME
        W.alphaMemItems.append(self)
        for c in self.successors:
            c.rightActivate(s)
        self.empty = False


class AlphaFilter(AlphaMemory):
    """An alphaFilter connects an alpha node to a join node. It has the full pattern, and
generates variable bindings
"""
    def __init__(self, pattern, vars, context, index, parents,
                 supportBuiltin, reachedGoal):
        self.index = index
        self.penalty = 10
        self.parents = parents
        self.pattern = pattern
        self.supportBuiltin = supportBuiltin
        self.context = context
        self.reachedGoal = reachedGoal
        freeVariables = vars
        def findExistentials(x):
            if hasattr(x, 'spo'):
                return findExistentials(x.spo())
            elif hasattr(x, 'existentials'):
                ex = frozenset(x.existentials())
                for s in x.statements:
                    ex = ex | findExistentials(s)
                return ex
            elif hasattr(x, '__iter__'):
                ex = frozenset()
                for n in x:
                    ex = ex | findExistentials(n)
                return ex
            else:
                return frozenset()
        existentialVariables = findExistentials(pattern)
        self.vars = pattern.occurringIn(freeVariables | existentialVariables)
        self.initialized = False
        AlphaMemory.__init__(self)

    def initialize(self, addToParents=True):
        if self.initialized:
            return
        self.initialized = True
        for primaryAlpha in self.parents:
            if addToParents:
                primaryAlpha.successors.appendleft(self)
            for triple in primaryAlpha:
                self.rightActivate(triple)

    def __len__(self):
        if self.initialized:
            return AlphaMemory.__len__(self)
        return reduce(operator.add, [len(x) for x in self.parents], 0)

    @property
    def provides(self):
        return self.vars

    @property
    def requires(self):
        return frozenset()

    def buildVarIndex(self, successor):
        return tuple(sorted(list(self.vars & successor.vars)))


    varCounter = itertools.count()
    def rightActivate(self, s):
        """This actually handles right-activation from (say) someone adding a
        triple matching this pattern."""
        # AlphaMemory is shared, so this is a bad idea.
#        if self.reachedGoal():
#            # We reached a goal, so this rule should be turned off.
#            print "ALREADY REACHED A GOAL!"
#            return
#
        if s.variables:
            var_bindings = {}
            for var in s.variables:
                newVar = s.context().newSymbol('http://example.com/alphaFilter#var%s' % self.varCounter.next())
                var_bindings[var] = newVar
                newVar.isVariable = True
            try:
                s2 = s.substitution(var_bindings)
            except TypeError:
                raise ValueError(s, type(s))
            s2.variables  = frozenset(var_bindings.values())
        else:
            s2 = s
        for  unWantedBindings, env in unify(s2, self.pattern, vars = self.vars | s2.variables): #
            if s2.variables.intersection(env.asDict().values()):
                print 'we have trouble with %s' % s2.variables.intersection(env.asDict().values())
                # We are in trouble here!
            if fullUnify or not frozenset(unWantedBindings.asDict().values()).difference(self.vars): # bad, but speeds things up
                self.add(TripleWithBinding(s, env))

    @classmethod
    def build(cls, index, pattern, vars, context, supportBuiltin, reachedGoal):
        secondaryAlpha = cls.construct(index, pattern, vars, context,
                                       supportBuiltin, reachedGoal)
        secondaryAlpha.initialize()
        return secondaryAlpha

    @classmethod
    def construct(cls, index, pattern, vars, context, supportBuiltin,
                  reachedGoal):
        """Constructs an AlphaFilter object for the specified
        pattern/vars/context, etc. and attaches it as the object stored in the
        index of the context IndexedFormula."""
        def replaceWithNil(x):
            if isinstance(x, Formula) or x.occurringIn(vars):
                return None
            return x
        masterPatternTuple = tuple(replaceWithNil(x) for x in (pattern.predicate(),
                                                         pattern.subject(),
                                                         pattern.object()))

        parents = []
        secondaryAlpha = cls(pattern, vars, context, index, parents,
                             supportBuiltin, reachedGoal)
        # Get the master pattern tuple for this AlphaFilter and use it
        # to construct each relevant pattern tuple used for indexing
        # in the IndexedFormula to connect this AlphaFilter to the
        # corresponding node.
        p, s, o = masterPatternTuple
        V = VAR_PLACEHOLDER
        pts = [(p, s, o)]
        for loc in 0, 1, 2:
            if masterPatternTuple[loc] is not None:
                newpts = []
                for t in pts:
                    newtuple = list(t)
                    newtuple[loc] = V
                    newtuple = tuple(newtuple)
                    newpts.append(t)
                    newpts.append(newtuple)
                pts = newpts
        for patternTuple in pts:
            primaryAlpha = index.setdefault(patternTuple, AlphaMemory())
            parents.append(primaryAlpha)
            for secondaryAlpha2 in primaryAlpha.successors:
                if secondaryAlpha2.pattern == pattern:
                    return secondaryAlpha2
        return secondaryAlpha
        

    def triplesMatching(self, successor, env, includeMissing=False): # This is fast enough
        """This is the biggest function here.  It does the heavy lifting to
        find which triples match the alpha pattern, and tests and asserts any
        builtins associated with this AlphaMemory."""
        retVal = self   # No reason to do additional work here
        assert self.initialized
        builtInMade = []
        if isinstance(self.pattern.predicate(), BuiltIn) and self.pattern.predicate() != self.pattern.predicate().store.sameAs:
            # TODO: log:conclusion, air:conclusion
            if self.pattern.predicate() is self.pattern.context().store.supports:
                # We also need to support the air:supports predicate
                # as well, but that's handled differently, as we need
                # to instantiate another AIR reasoner rather than an
                # N3Logic reasoner.
                subject = self.pattern.substitution(env).subject()
                
                knowledgeBase = self.pattern.context().store.newFormula()
                knowledgeBase.loadFormulaWithSubstitution(subject, env)
                # Think over a new formula, not the old one, so that the output is not modified.
                cwmThink(knowledgeBase)
                knowledgeBase.close()
                node = compilePattern(knowledgeBase._index, self.pattern.object().statements, self.vars, self.context)
                def onSuccess((triples, environment, penalty)):
                    newAssumption = self.pattern.substitution(environment.asDict()).substitution(env)
                    #somebodyPleaseAssertFromBuiltin(self.pattern.predicate(), newAssumption)
                    
                    builtInMade.append(TripleWithBinding(newAssumption, environment))
                    self.supportBuiltin(builtInMade[-1].triple)
                def onFailure():
                    # Do nothing.
                    pass
                prod = ProductionNode(node, onSuccess, onFailure)
            elif self.pattern.predicate() is self.pattern.context().store.owlEntails:
                # We also need to support the air:supports predicate
                # as well, but that's handled differently, as we need
                # to instantiate another AIR reasoner rather than an
                # N3Logic reasoner.
                subject = self.pattern.substitution(env).subject()
                
                knowledgeBase = self.pattern.context().store.newFormula()
                knowledgeBase.loadFormulaWithSubstitution(subject, env)
                # Also load the OWL rules formula.
                owlFormula = self.pattern.context().store.load(OWL_RULES)
                knowledgeBase.loadFormulaWithSubstitution(owlFormula, env)
                # Think over a new formula, not the old one, so that the output is not modified.
                cwmThink(knowledgeBase)
                knowledgeBase.close()
                node = compilePattern(knowledgeBase._index, self.pattern.object().statements, self.vars, self.context)
                def onSuccess((triples, environment, penalty)):
                    newAssumption = self.pattern.substitution(environment.asDict()).substitution(env)
                    #somebodyPleaseAssertFromBuiltin(self.pattern.predicate(), newAssumption)
                    
                    builtInMade.append(TripleWithBinding(newAssumption, environment))
                    self.supportBuiltin(builtInMade[-1].triple)
                def onFailure():
                    # Do nothing.
                    pass
                prod = ProductionNode(node, onSuccess, onFailure)
            elif self.pattern.predicate() is self.pattern.context().store.airJustifies:
                from policyrunner import runPolicy as airThink
                subject = self.pattern.substitution(env).subject()
                # HACK! I don't know what base URI these really are!
                logs = subject[0]
                rules = subject[1]
                if len(subject) == 3:
                    filterProperties = list(subject[2])
                else:
                    filterProperties = ['http://dig.csail.mit.edu/TAMI/2007/amord/air#compliant-with', 'http://dig.csail.mit.edu/TAMI/2007/amord/air#non-compliant-with']
                f, workingContext = airThink([], [], logFormulaObjs=logs, ruleFormulaObjs=rules, filterProperties=filterProperties, store=self.pattern.context().store)
                node = compilePattern(workingContext._index, self.pattern.object().statements, self.vars, self.context)
                def onSuccess((triples, environment, penalty)):
                    newAssumption = self.pattern.substitution(environment.asDict()).substitution(env)
                    #somebodyPleaseAssertFromBuiltin(self.pattern.predicate(), newAssumption)
                    
                    builtInMade.append(TripleWithBinding(newAssumption, environment))
                    self.supportBuiltin(builtInMade[-1].triple)
                def onFailure():
                    # Do nothing.
                    pass
                prod = ProductionNode(node, onSuccess, onFailure)
            elif self.pattern.predicate() is self.pattern.context().store.includes:
                # log:includes references the (Indexed)Formula in the
                # subject and checks it for a pattern match.
                if not isinstance(self.pattern.substitution(env).subject(), Formula):
                    # This match... isn't.
                    if includeMissing:
                        return retVal + [TripleWithBinding(BogusTriple(self.pattern), Env())] + builtInMade
                    return retVal + builtInMade
                newIndex = self.pattern.substitution(env).subject()._index
                node = compilePattern(newIndex, self.pattern.object().statements, self.vars, self.context, ignoreBuiltins=True)
                def onSuccess((triples, environment, penalty)):
                    newAssumption = self.pattern.substitution(environment.asDict()).substitution(env)
                    #somebodyPleaseAssertFromBuiltin(self.pattern.predicate(), newAssumption)
                    
                    builtInMade.append(TripleWithBinding(newAssumption, environment))
                    self.supportBuiltin(builtInMade[-1].triple)
                def onFailure():
                    # Do nothing.
                    pass
                prod = ProductionNode(node, onSuccess, onFailure)
            elif self.pattern.predicate() is self.pattern.context().store.notIncludes:
                # log:notIncludes references the (Indexed)Formula in the
                # subject and checks it for a pattern match.
                newIndex = self.pattern.substitution(env).subject()._index
                node = compilePattern(newIndex, self.pattern.object().statements, self.vars, ignoreBuiltins=True)
                def onSuccess((triples, environment, penalty)):
                    # Do nothing.
                    pass
                def onFailure():
                    newAssumption = self.pattern.substitution(env.asDict()).substitution(env)
                    #somebodyPleaseAssertFromBuiltin(self.pattern.predicate(), newAssumption)
                    
                    builtInMade.append(TripleWithBinding(newAssumption, env))
                    self.supportBuiltin(builtInMade[-1].triple)
                prod = ProductionNode(node, onSuccess, onFailure)

            # Alright, if we are ACTING as a function, we need to bind
            # the object.
            elif self.pattern.predicateActsAs(self.pattern.freeVariables(),
                                              set(env.keys())) == Function:
                matchedPat = self.pattern.substitution(env)
                # Need to listify anything that is a term that
                # resolves to a list for list builtins to work (since
                # they don't execute with context)
                if isinstance(self.pattern.predicate(), ListBuiltIn) and isNonEmptyListTerm(matchedPat.subject(), self.context):
                    subject = listify(matchedPat.subject(), self.context)
                else:
                    subject = matchedPat.subject()
                object = self.pattern.predicate().evalObj(subject, None, None, None, None)
                if object is not None:
                    for binds, environment in unify(self.pattern.object(), object, vars = self.vars):
                        builtInMade.append(TripleWithBinding(matchedPat.substitution(binds), environment.flatten(binds)))
                        self.supportBuiltin(builtInMade[-1].triple)
            elif self.pattern.predicateActsAs(self.pattern.freeVariables(),
                                              set(env.keys())) == MultipleFunction:
                # Each item of the possible objects needs to be
                # returned as a separate triple matching.
                matchedPat = self.pattern.substitution(env)
                if isinstance(self.pattern.predicate(), ListBuiltIn) and isNonEmptyListTerm(matchedPat.subject(), self.context):
                    subject = listify(matchedPat.subject(), self.context)
                else:
                    subject = matchedPat.subject()
                for object in self.pattern.predicate().evalObj(subject, None, None, None, None):
                    for binds, environment in unify(self.pattern.object(), object, vars = self.vars):
                        builtInMade.append(TripleWithBinding(matchedPat.substitution(binds), environment.flatten(binds)))
                        self.supportBuiltin(builtInMade[-1].triple)
            elif self.pattern.predicateActsAs(self.pattern.freeVariables(),
                                              set(env.keys())) == ReverseFunction:
                # If we're acting as a ReverseFunction, bind the
                # result of the reverse function to the subject.
                matchedPat = self.pattern.substitution(env)
                if isinstance(self.pattern.predicate(), ListBuiltIn) and isNonEmptyListTerm(matchedPat.object(), self.context):
                    object = listify(matchedPat.object(), self.context)
                else:
                    object = matchedPat.object()
                subject = self.pattern.predicate().evalSubj(object, None, None, None, None)
                if subject is not None:
                    for binds, environment in unify(self.pattern.subject(), subject, vars = self.vars):
                        builtInMade.append(TripleWithBinding(matchedPat.substitution(binds), environment.flatten(binds)))
                        self.supportBuiltin(builtInMade[-1].triple)
            elif self.pattern.predicateActsAs(self.pattern.freeVariables(),
                                              set(env.keys())) == MultipleReverseFunction:
                # Each item of the possible subjects needs to be
                # returned as a separate triple matching.
                matchedPat = self.pattern.substitution(env)
                if isinstance(self.pattern.predicate(), ListBuiltIn) and isNonEmptyListTerm(matchedPat.object(), self.context):
                    object = listify(matchedPat.object(), self.context)
                else:
                    object = matchedPat.object()
                for subject in self.pattern.predicate().evalSubj(object, None, None, None, None):
                    for binds, environment in unify(self.pattern.subject(), subject, vars = self.vars):
                        builtInMade.append(TripleWithBinding(matchedPat.substitution(binds), environment.flatten(binds)))
                        self.supportBuiltin(builtInMade[-1].triple)
            elif self.pattern.predicateActsAs(self.pattern.freeVariables(),
                                              set(env.keys())) == BuiltIn:
                # If we're acting as a ReverseFunction, bind the
                # result of the reverse function to the subject.
                matchedPat = self.pattern.substitution(env)
                if isinstance(self.pattern.predicate(), ListBuiltIn) and isNonEmptyListTerm(matchedPat.subject(), self.context):
                    subject = listify(matchedPat.subject(), self.context)
                else:
                    subject = matchedPat.subject()
                if isinstance(self.pattern.predicate(), ListBuiltIn) and isNonEmptyListTerm(matchedPat.object(), self.context):
                    object = listify(matchedPat.object(), self.context)
                else:
                    object = matchedPat.object()
                if matchedPat.predicate().eval(subject, object,
                                               None, None, None, None):
                    builtInMade.append(TripleWithBinding(matchedPat, env))
                    self.supportBuiltin(builtInMade[-1].triple)
        if includeMissing:
            return retVal + [TripleWithBinding(BogusTriple(self.pattern), Env())] + builtInMade
        return retVal + builtInMade

class GoalAlphaFilter(AlphaFilter):
    """An AlphaFilter that explicitly matches goals (including non-binding
    goal-wildcard variables).  Most of this is similar to AlphaFilter
    except that we care about wildcards in the goal (where those
    wildcards can match anything, but the binding is irrelevant)"""
    def __init__(self, pattern, vars, context, index, parents,
                 goalWildcards, supportBuiltin):
        AlphaFilter.__init__(self, pattern, vars, context, index, parents,
                             supportBuiltin, lambda: False)
        self.goalWildcards = goalWildcards

    @classmethod
    def build(cls, index, pattern, vars, context, goalWildcards,
              supportBuiltin):
        secondaryAlpha = cls.construct(index, pattern, vars, context,
                                       goalWildcards, supportBuiltin)
        secondaryAlpha.initialize()
        return secondaryAlpha

    @classmethod
    def construct(cls, index, pattern, vars, context, goalWildcards,
                  supportBuiltin):
        def replaceWithNil(x):
            if isinstance(x, Formula) or x.occurringIn(vars) or x.occurringIn(goalWildcards.values()):
                return None
            return x
        masterPatternTuple = tuple(replaceWithNil(x) for x in (pattern.predicate(),
                                                         pattern.subject(),
                                                         pattern.object()))

        parents = []
        secondaryAlpha = cls(pattern, vars, context, index, parents,
                             goalWildcards, supportBuiltin)
        p, s, o = masterPatternTuple
        V = VAR_PLACEHOLDER
        pts = [(p, s, o)]
        for loc in 0, 1, 2:
            if masterPatternTuple[loc] is not None:
                newpts = []
                for t in pts:
                    newtuple = list(t)
                    newtuple[loc] = V
                    newtuple = tuple(newtuple)
                    newpts.append(t)
                    newpts.append(newtuple)
                pts = newpts
        for patternTuple in pts:
            primaryAlpha = index.setdefault(patternTuple, AlphaMemory())
            parents.append(primaryAlpha)
            for secondaryAlpha2 in primaryAlpha.successors:
                if secondaryAlpha2.pattern == pattern:
                    return secondaryAlpha2
        return secondaryAlpha

    varCounter = itertools.count()
    def rightActivate(self, s):
        # Like the original AlphaFilter.rightActivate except that we
        # also handle wildcards.

        # Wildcards /always/ match, but do so without binding
        # pattern variables.

        if s.variables:
            var_bindings = {}
            for var in s.variables:
                newVar = s.context().newSymbol('http://example.com/alphaFilter#var%s' % self.varCounter.next())
                var_bindings[var] = newVar
                newVar.isVariable = True
            try:
                s2 = s.substitution(var_bindings)
            except TypeError:
                raise ValueError(s, type(s))
            s2.variables  = frozenset(var_bindings.values())
        else:
            s2 = s
        for  unWantedBindings, env in unify(s2, self.pattern, vars = self.vars | s2.variables | set(self.goalWildcards.values())): #
            # ONLY FOR WILDCARDS: Filter bindings to wildcards.
            # TODO: Weird bindings here (for compliance)
            # TODO: Excess rdf:types!!
            env = Env(Env(), dict([(item[0], (item[1], Env())) for item in env.asDict().items() if item[1] not in self.goalWildcards.values()]))
            if s2.variables.intersection(env.asDict().values()):
                print 'we have trouble with %s' % s2.variables.intersection(env.asDict().values())
                # We are in trouble here!
            self.add(TripleWithBinding(s, env))

    def triplesMatching(self, successor, env, includeMissing=False):
        # Like the original AlphaFilter.triplesMatching except that we
        # can JUST do pattern-matching.
        retVal = self   # No reason to do additional work here
        assert self.initialized
        # NOTE: No builtins!
        return retVal

class Token(object):
    """A token is a partial match, stored in a beta node
"""
    def __init__(self, node, parent, current, env, penalty=0):
        """It is not the job of this function to compute
        the new env; indeed, because that operation
        could fail.


        """
        self.penalty = penalty + parent.penalty
        self.parent = parent
        assert not isinstance(current, TripleWithBinding)
        self.current = current
        self.node = node
        self.children = set()
        self.env = env
        parent.children.add(self)
        current.WME.tokens.add(self)

    def moveDown(self, node):
        return Token(node, self.parent, self.current, self.env, self.penalty)

    def delete(self):
        self.parent.children.remove(self)
        while self.children:
            t = self.children.pop()
            t.delete()
        self.node.removeItem(self)
        W = self.current.WME

    def fail(self):
        self.parent.children.remove(self)
        self.current.WME.tokens.remove(self)

    def flatten(self):
        retVal, _, __ = self.parent.flatten()
        retVal.append(self.current)
        return (retVal, self.env, self.penalty)
                


class NullTokenClass(object):
    """There is one empty null token, representing an unstarted match.
"""
    __one__ = None
    def __new__(cls):
        if cls.__one__:
            return cls.__one__
        self = object.__new__(cls)
        cls.__one__ = self
        self.children = set()
        self.env = Env()
        self.penalty = 0
        return self

    def moveDown(self, node):
        return self

    def flatten(self):
        return ([], self.env, 0)
NullToken = NullTokenClass()


class ReteNode(object):
    def __new__(cls, parent):
        self = object.__new__(cls)
        self.parent = parent
        self.children = set()
        self.parent.children.add(self)
        if hasattr(parent, 'allChildren'):
            parent.allChildren.add(self)
        return self

class EmptyRootClass(ReteNode):
    """There is one empty root node, the root of the tree of rete nodes
It has nothing matched yet.
"""
    __one__ = None
    def __new__(cls):
        if cls.__one__:
            return cls.__one__
        self = object.__new__(cls)
        cls.__one__ = self
        self.items = set([NullToken])
        self.empty = False
        self.children = set()
        self.allChildren = set()
        self.vars = frozenset()
        self.varTuple = ()
        return self
EmptyRoot = EmptyRootClass()


class BetaMemory(ReteNode):
    """A beta memory stores Tokens, received from the one parent join node
"""
    def __new__(cls, parent, reachedGoal=lambda: False):
        for B in parent.children:
            if isinstance(B, cls):
                return B  # A join node should only have one child!
        self = ReteNode.__new__(cls, parent)
        self.reachedGoal = reachedGoal
        self.items = set()
        self.allChildren = set()
        self.empty = True
        self.vars = self.parent.vars
        self.updateFromAbove()
        return self

    def leftActivate(self, token, triple, newBinding, penalty=0):
        if self.reachedGoal():
            # We reached a goal, so this rule should be turned off.
#            print "ALREADY REACHED A GOAL!"
            return

        newToken = Token(self, token, triple, newBinding, penalty=penalty)
        if newToken.penalty > 10:
            newToken.fail()
            return
        self.items.add(newToken)
        for c in self.children.copy():
            c.leftActivate(newToken)
        self.empty = False


    def updateFromAbove(self):
        if self.reachedGoal():
            # We reached a goal, so this rule should be turned off.
#            print "ALREADY REACHED A GOAL!"
            return

        parent = self.parent
        parentChildren = parent.children
        parent.children = set([self])
        for item in parent.parent.items.copy():
            parent.leftActivate(item)
        parent.children = parentChildren

    def removeItem(self, item):
        try:
            self.items.remove(item)
        except KeyError:
            raise ValueError(item.flatten(), [x.flatten() for x in self.items])
        if not self.items:
            self.empty = True
            for c in self.children:
                if isinstance(c, JoinNode):
                    dequeRemove(c.alphaNode.successors, c)

class JoinNode(ReteNode):
    """A join node combines matches from a beta memory and an alphaFilter
to get larger matches.
"""
    def __new__(cls, parent, alphaNode, buildGoals=False, reachedGoal=lambda: False):
        for child in parent.allChildren:
            if isinstance(child, cls) and child.alphaNode is alphaNode:
                return child
        self = ReteNode.__new__(cls, parent)
        self.reachedGoal = reachedGoal
        self.alphaNode = alphaNode
        self.vars = self.parent.vars | self.alphaNode.vars
        if not parent.empty:
            self.alphaNode.successors.appendleft(self)
            if alphaNode.empty:
                parent.children.remove(self)
        self.varIndex = self.alphaNode.buildVarIndex(self)
        if buildGoals:
            raise BogusTripleError('Goal building is dead. Long live goal building')
        self.makesGoals = buildGoals
        return self

    def leftActivate(self, token):
        if self.reachedGoal():
            # We reached a goal, so this rule should be turned off.
#            print "ALREADY REACHED A GOAL!"
            return

        if self.parent.empty:
            self.relinkAlpha()
            # Only delink this join if it's not for a triple that
            # isn't a BuiltIn...
            if not isinstance(self.alphaNode.pattern.predicate(), BuiltIn) and self.alphaNode.empty:
                self.parent.children.remove(self)
        matchedSomething = False
        for i in self.alphaNode.triplesMatching(self, token.env, self.makesGoals):
            triple = i.triple
            env = i.env
            newBinding = self.test(token, env)
            if newBinding is not None:
                matchedSomething = True
                for c in self.children:
                    c.leftActivate(token, triple, newBinding)


    def test(self, token, env2):  # Not good enough! need to unify somehow....
        env = token.env
        newEnv = env
##        newEnvs = [Env()]
##        allKeys = frozenset(env1.keys()) | frozenset(env2.keys())
##        for key in allKeys:
##            val1, source1 = env1.dereference(key)
##            val2, source2 = env1.dereference(key)
##            oldNewEnvs = newEnvs
##            newEnvs = []
##            for newEnv in oldNewEnvs:
##                newEnvs.extend([x[0] for x in unify(val1, val2, vars=self.vars, bindings=newEnv, n1Source=source1, n2Source=source2) ])
##        print newEnvs
##        if not newEnvs:
##            return None
##        return newEnvs[0]
                    


        
        for var, (val, source) in env2.items():
            if var in env:
                if env[var] == val:
                    pass
                else:
                    return None
            else:
                newEnv = newEnv.bind(var, (val, source))
        return newEnv

    def rightActivate(self, triple_holder):
        if self.reachedGoal():
            # We reached a goal, so this rule should be turned off.
#            print "ALREADY REACHED A GOAL!"
            return

        if self.alphaNode.empty:
            self.relinkBeta()
            if self.parent.empty:
                dequeRemove(self.alphaNode.successors, self)
        triple = triple_holder.triple
        env = triple_holder.env
        for token in self.parent.items:
            newBinding = self.test(token, env)
            if newBinding is not None:
##                if token in self.falseMatches:
##                    falseTriple = self.falseMatches[token]
##                    del self.falseMatches[token]
##                    if False and self.retractTriple is not None:
##                        self.retractTriple(falseTriple)
                for c in self.children:
                    c.leftActivate(token, triple, newBinding)
        

    def relinkAlpha(self):
        self.alphaNode.successors.appendleft( self)
    def relinkBeta(self):
        self.parent.children.add(self)



class GoalJoinNode(ReteNode):
    """Like a JoinNode, except that it may be right-activated by any one
    of many GoalAlphaFilters."""
    def __new__(cls, parent, alphaNodes):
        # The first alphaNode will be treated as the parent.
        for child in parent.allChildren:
            if isinstance(child, cls) and child.alphaNodes == alphaNodes and all([x is y for (x, y) in zip(child.alphaNodes, alphaNodes)]):
                return child
        self = ReteNode.__new__(cls, parent)
        self.alphaNodes = alphaNodes
        self.vars = self.parent.vars
        for alphaNode in self.alphaNodes:
            self.vars |= alphaNode.vars
        if not parent.empty:
            for alphaNode in self.alphaNodes:
                alphaNode.successors.appendleft(self)
        return self

    def relinkAlphas(self):
        for alphaNode in self.alphaNodes:
            alphaNode.successors.appendleft( self)
    def relinkBeta(self):
        self.parent.children.add(self)

    def leftActivate(self, token):
        if self.parent.empty:
            self.relinkAlphas()
            # Only delink this join if it's not for a triple that
            # isn't a BuiltIn...
            for alphaNode in self.alphaNodes:
                if not isinstance(alphaNode.pattern.predicate(), BuiltIn) and alphaNode.empty:
                    self.parent.children.remove(self)
        matchedSomething = False
        for alphaNode in self.alphaNodes:
            for i in alphaNode.triplesMatching(self, token.env, False):
                # No merging.  Just leftActivate the children.
                triple = i.triple
                env = i.env
                for c in self.children:
                    c.leftActivate(token, triple, env)

    def test(self, token, env2):  # Not good enough! need to unify somehow....
        env = token.env
        newEnv = env
        
        for var, (val, source) in env2.items():
            if var in env:
                if env[var] == val:
                    pass
                else:
                    return None
            else:
                newEnv = newEnv.bind(var, (val, source))
        return newEnv

    def rightActivate(self, triple_holder):
        if all([x.empty for x in self.alphaNodes]):
            self.relinkBeta()
            if self.parent.empty:
                for alphaNode in self.alphaNodes:
                    dequeRemove(alphaNode.successors, self)
        triple = triple_holder.triple
        env = triple_holder.env
        for token in self.parent.items:
            newBinding = self.test(token, env)
            if newBinding is not None:
##                if token in self.falseMatches:
##                    falseTriple = self.falseMatches[token]
##                    del self.falseMatches[token]
##                    if False and self.retractTriple is not None:
##                        self.retractTriple(falseTriple)
                for c in self.children:
                    c.leftActivate(token, triple, newBinding)
    
class ProductionNode(ReteNode):
    """A production node sits at the leaf of the node tree,
with a method to call when the match succeeds
"""
    def __new__(cls, parent, task, alternative = None):
        self = ReteNode.__new__(cls, parent)
        self.items = set()
        self.task = task
        self.alternative = alternative
        self.updateFromAbove()
        if not self.items:
            self.alternative()
        return self

    def leftActivate(self, token):
        token = token.moveDown(self)
        self.items.add(token)
        self.task(token.flatten())

    def updateFromAbove(self):
        for token in frozenset(self.parent.items):
            self.leftActivate(token)

    def removeItem(self, item):
        self.items.remove(item)
        if not self.items:
            if self.alternative:
                self.alternative()



#####=================

def compilePatternTester(parentFormula, patternFormula):
    index = parentFormula._index
    patterns = patternFormula.statements
    vars = parentFormula.universals()
    bottomBeta = compilePattern(index, patterns, vars)
    def onSuccess((triples, env)):
        print 'success, pattern=%s, triples=%s, env=%s' % (patterns, triples, env)
    def onFailure():
        print 'failure, pattern=%s' % patterns
    trueBottom = ProductionNode(bottomBeta, onSuccess, onFailure)
    return trueBottom

def test():
	socrates = store.newSymbol('http://www.example.com/#socrates')
	ty = store.first.resource['type']
	man = store.newSymbol('http://www.example.com/#Man')
	X = workingContext.newUniversal('http://www.example.com/#X')
	f = store.newFormula()
	f.add(X, ty, man)
	f = f.close()
	b = rete.compilePatternTester(workingContext, f)
	workingContext.add(socrates, ty, man)
	workingContext.removeStatement(workingContext.statementsMatching(subj=socrates, pred=ty, obj=man)[0])
	return b


def test2():
	socrates = store.newSymbol('http://www.example.com/#socrates')
	ty = store.first.resource['type']
	man = store.newSymbol('http://www.example.com/#Man')
	X = workingContext.newUniversal('http://www.example.com/#X')
	f = store.newFormula()
	f.add(X, ty, man)
	f = f.close()
	workingContext.add(socrates, ty, man)
	b = rete.compilePatternTester(workingContext, f)
	workingContext.removeStatement(workingContext.statementsMatching(subj=socrates, pred=ty, obj=man)[0])
	return b


def test3():
	socrates = store.newSymbol('http://www.example.com/#socrates')
	aristotle = store.newSymbol('http://www.example.com/#Aristotle')
	ty = store.first.resource['type']
	man = store.newSymbol('http://www.example.com/#Man')
	greek = store.newSymbol('http://www.example.com/#Greek')
	X = workingContext.newUniversal('http://www.example.com/#X')
	f = store.newFormula()
	f.add(X, ty, man)
	f.add(X, ty, greek)
	f = f.close()
	workingContext.add(socrates, ty, greek)
	workingContext.add(aristotle, ty, man)
	b = rete.compilePatternTester(workingContext, f)
	workingContext.add(socrates, ty, man)
	workingContext.removeStatement(workingContext.statementsMatching(subj=socrates, pred=ty, obj=man)[0])
	return b


def printRete():
    knownNodes = set([EmptyRoot])
    alreadyPrinted = set()
    while knownNodes.difference(alreadyPrinted):
        node = knownNodes.difference(alreadyPrinted).pop()
        alreadyPrinted.add(node)
        print 'node %s' % node
        print '  of type %s' % node.__class__.__name__
        print '  ---> children %s' % node.children
        if hasattr(node, 'items'):
            print '  ----> items : %s' % [x.flatten() for x in node.items]
        if hasattr(node, 'alphaNode'):
            print ' ----> alphaNode %s' % node.alphaNode
            print ' ----------> pattern %s' % node.alphaNode.pattern
            print ' ----------> vars %s' % node.alphaNode.vars
        if hasattr(node, 'allChildren'):
            print ' ----> allChildren %s' % node.allChildren
            for c in node.allChildren:
                knownNodes.add(c)
        for c in node.children:
            knownNodes.add(c)

        
        
    

if __name__ == '__main__':
    from tmswap import rete
    from tmswap import llyn
    store = llyn.RDFStore()
    workingContext = store.newFormula()
    workingContext.stayOpen = True
    test()
    test2()
    test3()
