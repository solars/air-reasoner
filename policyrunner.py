#!/usr/bin/env python
"""Amord.py

an attempt at an amord implementation built on cwm

"""

import os

import weakref
#WVD = weakref.WeakValueDictionary
WVD = dict
from collections import deque
from itertools import chain

import llyn
from formula import Formula, StoredStatement
from term import List, Env, Symbol, Term, BuiltIn, SUBJ, PRED, OBJ

import uripath

import diag
progress = diag.progress

import tms
import rete
import treat

MM = rete # or it could be treat

OFFLINE = [False]

airstatementWarnings = set()

from prooftrace import (supportTrace,
                        removeFormulae,
                        removeBaseRules,
                        simpleTraceOutput,
                        rdfTraceOutput)

from py25 import all, any, defaultdict
                        

workcount = defaultdict(int)

GOAL = 1

debugLevel = 0

# Need to extend the List and Tuple types to do x.substitution()
def baseSub(value, bindings):
    if value is not None:
        return value.substitution(bindings)
    else:
        return None

class SubstitutingList(list):
    """A list that responds to the substitution method"""
    pass

SubstitutingList.substitution = \
    lambda self, bindings: \
        SubstitutingList(map(lambda x: baseSub(x, bindings), self))

class SubstitutingTuple(tuple):
    """A tuple that responds to the substitution method."""
    pass

SubstitutingTuple.substitution = \
    lambda self, bindings: \
        SubstitutingTuple(map(lambda x: baseSub(x, bindings), self))

class FormulaTMS(object):
    """This is the interface between the TMS and the rdf side of things
It keeps a Formula of all facts currently believed.
The job of activating rules also goes on this in the event() method.
"""
    tracking = True
    def __init__(self, workingContext):
        """Create the TMS to be associated with the specified working context
        (IndexedFormula)."""
        self.tms = tms.TMS('FormulaTMS', self.event)
        self.nodes = WVD()
        self.workingContext = workingContext
        workingContext.tms = self
        self.NS = workingContext.newSymbol('http://dig.csail.mit.edu/2007/cwmrete/tmswap/amord')
        self.formulaContents = self.NS['FormulaContents']
        self.parseSemantics = workingContext.store.semantics
        self.premises = set()
        self.falseAssumptions = set()
        self.contexts = {}

        self.assumedPolicies = []
        self.assumedURIs = []
        self.assumedStrings = []
        self.assumedClosedWorlds = []
        
        # Stow some environments associated with things on the side.
        self.envs = {}

    def getAuxTriple(self, auxid, subject, predicate, object, variables):
        """An aux triple is a triple supported for something other than belief
This is currently only used for goals
"""
        if (auxid, subject, predicate, object, variables) not in self.nodes:
            a = tms.Node(self.tms, (auxid, subject, predicate, object, variables))
            self.nodes[(auxid, subject, predicate, object, variables)] = a
        return self.nodes[(auxid, subject, predicate, object, variables)]

    def justifyAuxTriple(self, auxid, subject, predicate, object, variables, rule, antecedents):
        """Get an aux triple (e.g. for goals) and justify it with the
        specified antecedents and rule."""
        auxnode = self.getAuxTriple(auxid, subject, predicate, object, variables)
        node = self.getTriple(subject, predicate, object)
        a = tms.AndExpression(list(antecedents))
        n = tms.NotExpression(node)
        auxnode.justify(rule, [a,n])


    def getContext(self, id):
        """Get the context (other than the working context) which matches the
        specified ID (e.g. GOAL context as opposed to the working
        context)"""
        if id not in self.contexts:
            self.contexts[id] = self.workingContext.newFormula()
        return self.contexts[id]

    def getTriple(self, subject, predicate, object, variables=None):
        """Get the TMS node which corresponds to the given triple."""
        if (subject, predicate, object, variables) not in self.nodes:
            a = tms.Node(self.tms, (subject, predicate, object, variables))
            self.nodes[(subject, predicate, object, variables)] = a
        return self.nodes[(subject, predicate, object, variables)]

    def getThing(self, thing):
        """Get a value stored in the TMS (e.g. thing may be a tuple (subject,
        predicate, object, variables), or a Rule object)"""
        if thing not in self.nodes:
            a = tms.Node(self.tms, thing)
            self.nodes[thing] = a
        return self.nodes[thing]
    
    def getThingWithEnv(self, thing, env):
        if thing not in self.envs:
            self.envs[thing] = env
        return self.getThing(thing)
    
    def getStatement(self, (subject, predicate, object, variables)):
        """Get the statement which matches the specified SPO triple from the
        corresponding working context."""
        return self.workingContext.statementsMatching(subj=subject, pred=predicate, obj=object)[0]

    def getAuxStatement(self, (auxid, subject, predicate, object, variables)):
        """Get the aux triple which matches the specified SPO triple.  Note
        that auxid is used to select the auxiliary context (e.g. GOAL)."""
        return self.getContext(auxid).statementsMatching(subj=subject, pred=predicate, obj=object)[0]

    def event(self, node, justification):
        """Called when justifying a TMS node using Node.justify().  Examines
        the datum of the node and activates or deactivates the node
        appropriately (e.g. by constructing a Rete if it is a rule, asserting
        or removing a triple if it is an Assertion, etc."""
        # Add a premise
        if isinstance(justification, tms.Premise):
            if isinstance(node.datum, tuple) and len(node.datum) == 2:
                pass # Better than the alternative?
            else:
                self.premises.add(node)

        # Remove statements if the justification is false.
        if justification is False:
            if isinstance(node.datum, Rule):
                pass # not worth the work
            elif isinstance(node.datum, ImportedRuleset):
                pass
            if isinstance(node.datum, tuple):
                if len(node.datum) == 4:
                    self.workingContext.removeStatement(self.getStatement(node.datum))
#                    self.getContext(GOAL).removeStatement(self.getStatement(node.datum))
                else:
                    self.getContext(GOAL).removeStatement(self.getAuxStatement(node.datum))

        # Assert/activate/compile a rule.
        if isinstance(node.datum, Rule):
            if debugLevel >= 3:
                if node.datum.goal:
                    progress('\tNow supporting goal rule %s because of %s' % (node, justification))
                else:
                    progress('\tNow supporting rule %s because of %s' % (node, justification))
            if self.tracking:
                if node.datum.goal:
                    workcount['goal-rule'] += 1
                else:
                    workcount['rule'] += 1
            node.datum.compileToRete()
            if debugLevel >= 4:
                progress('\t\t ... built rule')
        elif isinstance(node.datum, ImportedRuleset):
            # Actually import the ruleset.
            # TODO: Relative uris
            pf = store.load(node.datum.uri.uriref())
            # ruleAssumptions
            policies, rules, goal_rules, cwm_rules = Rule.compileFormula(node.datum.eventLoop, node.datum.tms, pf, base=False, goalWildcards=goalWildcards)
            node.datum.tms.assumedPolicies.extend(policies)
            verbose = False
            if verbose:
                print 'run-time import of ', node.datum.uri
                print 'rules = ', rules + cwm_rules
                print 'goal rules = ', goal_rules
            for rule in rules + goal_rules + cwm_rules:
                a  = node.datum.tms.getThing(rule)
                #ruleAssumptions.append(a)
                a.assume()
        if isinstance(node.datum, Symbol):
            if debugLevel >= 2:
                progress('Now supporting %s because of %s' % (node, justification))
            f = _loadF(self, node.datum.uriref())
            self.getThing(f).justify(self.parseSemantics, [node])
        if isinstance(node.datum, Formula):
            if debugLevel >= 2:
                progress('Now supporting %s because of %s' % (node, justification))
            self.workingContext.loadFormulaWithSubstitution(node.datum)
        if isinstance(node.datum, tuple):
#            print '\t ... now supporting %s because of %s' % (node, justification)
            if len(node.datum) == 2:
                pass # signal data
            elif len(node.datum) == 4:
                if isinstance(node.datum[1], BuiltIn) and node.datum[1] is not node.datum[1].store.sameAs:
                     # Hackety hack...  Dun like it, but it avoids
                     # inserting a (wrong) built-in fact...
                     return
                if self.tracking:
                    workcount['fact'] += 1
                triple = node.datum[:3]
                variables = node.datum[3]
                if variables is None:
                    self.workingContext.add(*triple)                
#                    self.getContext(GOAL).add(*triple)
                else:  # slow path --- do we need it?
                    s, p, o = triple
                    s1 = self.workingContext._buildStoredStatement(subj=s,
                                                                 pred=p,
                                                                 obj=o,
                                                                why=None)
                    if isinstance(s1, int): # It is possible for cwm to not directly add things
                        raise TypeError(node)
                    s1.variables = v
                    result = self.workingContext. _addStatement(s1)
                    
#                    s2 = getContext(GOAL)._buildStoredStatement(subj=s,
#                                                                 pred=p,
#                                                                 obj=o,
#                                                                why=None)
#                    if isinstance(s2, int):
#                        raise TypeError(node)
#                    s2.variables = v
#                    result = self.getContext(GOAL). _addStatement(s1)
            else:
                if self.tracking:
                    workcount['goal'] += 1
                if debugLevel > 7:
                    progress('\t ... now supporting goal %s because of %s' % (node, justification))
                c, s, p, o, v = node.datum
                statement = self.getContext(c)._buildStoredStatement(subj=s,
                                                                 pred=p,
                                                                 obj=o,
                                                                why=None)
                if isinstance(statement, int):
                    raise TypeError(node)
                statement.variables = v
                result = self.getContext(c). _addStatement(statement)
#                print 'added %s, a total of %s statements' % (statement, result)
                
        

def canonicalizeVariables(statement, variables):
    """Canonicalize all variables in statement to be URIs of the form
    http://example.com/vars/#variable(digits).  Returns a tuple
    corresponding to the subject, predicate, and object following
    canonicalization, and a frozenset including the URIs of all
    variables that were canonicalized.
    
    """
    subj, pred, obj = statement.spo()
    store = statement.context().store
    varMapping = {}
    count = [0]
    def newVariable():
        count[0] += 1
        return store.newSymbol('http://example.com/vars/#variable%s' % count[0])
    def canNode(node):
        if node in varMapping:
            return varMapping[node]
        if node in variables:
            varMapping[node] = newVariable()
            return varMapping[node]
        if isinstance(node, List):
            return node.store.newList([canNode(x) for x in node])
        if isinstance(node, Formula):
            # Commenting this out for log:includes.  What side-effects
            # does this have?
#            if node.occurringIn(variables):
#                raise ValueError(node)
#            return node
            
            # log:includes uses external scope to canonicalize
            # variables...?
            f = None
            for statement in node.statements:
                subj, pred, obj = statement.spo()
                if subj is not canNode(subj) or pred is not canNode(pred) or obj is not canNode(obj):
                    f = node.store.newFormula()
            if f:
                for statement in node.statements:
                    subj, pred, obj = statement.spo()
                    f.add(canNode(subj), canNode(pred), canNode(obj))
                f.close()
                return f
        return node
    return (canNode(subj), canNode(pred), canNode(obj)), frozenset(varMapping.values())

class Assertion(object):
    """An assertion is something which can be asserted (e.g. triples). It tracks what its support (dependencies) will be when asserted
"""
    def __init__(self, pattern, support=None, rule=None, validToRename=None):
        self.pattern = pattern
        self.support = support
        self.rule = rule
        
        if isinstance(pattern, Formula):
            if validToRename is None:
                self.needsRenaming = frozenset(pattern.existentials())
            else:
                self.needsRenaming = frozenset(pattern.existentials()).intersection(validToRename)
        else:
            self.needsRenaming = frozenset()

    def substitution(self, bindings):
        if self.support is None:
            support = None
        else:
            supportList = []
            for x in self.support:
                if isinstance(x, frozenset):
                    supportList.append(x)
                else:
                    supportList.append(x.substitution(bindings))
            support = frozenset(supportList)

        newBlankNodesBindings = dict([(x, self.pattern.newBlankNode()) for x in self.needsRenaming]) # if invalid, will not be run
        bindings.update(newBlankNodesBindings)

        return Assertion(self.pattern.substitution(bindings), support, self.rule, validToRename=newBlankNodesBindings.values())

    def __repr__(self):
        return 'Assertion(%s,%s,%s)' % (self.pattern, self.support, self.rule)

    def __eq__(self, other):
        return isinstance(other, Assertion) and self.pattern == other.pattern

    def __hash__(self):
        return hash((self.pattern, self.__class__)) 

    
        

class AuxTripleJustifier(object):
    """A thunk, to separate the work of creating aux triples from
building the rules whose support created them.
These are then passed to the scheduler to be evaluated at an appropriate time.
"""
    def __init__(self, tms, *args):
        self.tms = tms
        self.args = args

    def __call__(self, eventLoop=None):
        self.tms.justifyAuxTriple(*self.args)

class RuleName(object):
    """A name for a rule (???)"""
    def __init__(self, name, descriptions, prompts):
        assert isinstance(name, Term)
        assert all(isinstance(x, Term) for x in descriptions)
        assert all(isinstance(x, Term) for x in prompts)
        self.name = name
        self.descriptions = descriptions
        self.prompts = prompts

    def __repr__(self):
        return 'R(%s)' % (self.name,)

    def uriref(self): # silly
        return self.name.uriref() + '+'.join(''.join(str(y) for y in x) for x in self.descriptions)


class RuleFire(object):
    """A thunk, passed to the scheduler when a rule fires, to be called at
    the earliest convenience.  When called, it handles the appropriate
    action for the rule (e.g. to justify a triple in the TMS (based on a
    closed world if needed), activate a nested rule, or import a ruleset.
"""
    def __init__(self, rule, triples, env, penalty, result, alt=False):
        self.rule = rule
        self.args = (triples, env, penalty, result, alt)

    def __call__(self, eventLoop):
        triples, env, penalty, result, alt = self.args
        self = self.rule
        if alt and self.success: # We fired after all
#            raise RuntimeError('we do not have any alts yet')
            return
        if debugLevel > 12:
            if alt:
                progress('%s failed, and alt is being called' % (self.label,))
            else:
                progress('%s succeeded, with triples %s and env %s' % (self.label, triples, env))
        triplesTMS = []
        goals = []
        unSupported = []
        # Iterate through each triple of this RuleFire event, and
        # classify it as supported (triplesTMS), a goal triple
        # (goals), or unsupported (unSupported)
        for triple in triples:
            t = self.tms.getTriple(*triple.spo())
            if t.supported:
                triplesTMS.append(t)
            else:
                t2 = self.tms.getAuxTriple(GOAL, triple.subject(), triple.predicate(), triple.object(), triple.variables)
                if t2.supported:
                    goals.append((triple, t2))
                else:
                    unSupported.append(triple)

        if self.matchName:
            if self.matchName in env:
                return
            env = env.bind(self.matchName, (frozenset(triplesTMS + [x[1] for x in goals]), Env()))
        if goals and unSupported:
            raise RuntimeError(goals, unSupported) #This will never do!
        elif goals:
            # Goal-rule stuff below.
            if not self.goal:
                raise RuntimeError('how did I get here?\nI matched %s, which are goals, but I don\'t want goals' % goals)
#                print 'we goal succeeded! %s, %s' % (triples, result)
            envDict = env.asDict()
            for triple, _ in goals:
                assert not triple.variables.intersection(env.keys())
                newVars = triple.variables.intersection(envDict.values())
                if newVars:
                    raise NotImplementedError("I don't know how to add variables")
            
            for r in result:
                # Do any substitution and then extract the description
                # and r12 from the particular r's tuple.
                r12 = r.substitution(env.asDict())
                prompt = r12[2]
                desc = r12[1]
                r12 = r12[0]
                
                r2 = r12.pattern
                support = r12.support
                ruleId = r12.rule
                assert isinstance(r2, Rule) or isinstance(r2, ImportedRuleset) or not r2.occurringIn(self.vars), (r2, env, penalty, self.label)
#            print '   ...... so about to assert %s' % r2
                r2TMS = self.tms.getThingWithEnv(r2, env)
                if support is None:
                    if isinstance(r2, Rule):
                        r2TMS.justify(self.sourceNode, triplesTMS + [self.tms.getThing(self)])
                    elif isinstance(r2, ImportedRuleset):
                        r2TMS.justify(self.sourceNode, triplesTMS + [self.tms.getThing(self)])
                    else:
                        # Delay the justification of assertions in else clauses.
                        eventLoop.addAssertion(lambda: r2TMS.justify(self.sourceNode, triplesTMS + [self.tms.getThing(self)]))
                else:
                    supportTMS = reduce(frozenset.union, support, frozenset())
                    if isinstance(r2, Rule):
                        r2TMS.justify(ruleId, supportTMS)
                    elif isinstance(r2, ImportedRuleset):
                        r2TMS.justify(ruleId, supportTMS)
                    else:
                        eventLoop.addAssertion(lambda: r2TMS.justify(ruleId, supportTMS))
                        eventLoop.addAssertion(lambda: r2TMS.justify(RuleName(ruleId, desc, prompt), supportTMS))
#                assert self.tms.getThing(self).supported
#                assert r2TMS.supported                
#                raise NotImplementedError(goals) #todo: handle goals
        elif unSupported:
            raise RuntimeError(triple, self) # We should never get unsupported triples
        else:
            if self.goal:
                return
#                print 'we succeeded! %s, %s' % (triples, result)
            if alt:
                # Close the world over what we've currently assumed if
                # this RuleFire event is an alternate one (else)
#                closedWorld = self.tms.getThing(('closedWorld', self.tms.workingContext.newList(list(self.tms.premises))))
                closedWorld = self.tms.getThing(('closedWorld',
                                                 self.tms.workingContext.newList(self.tms.assumedPolicies +
                                                     self.tms.assumedURIs +
                                                     self.tms.assumedStrings +
                                                     self.tms.assumedClosedWorlds)))
                closedWorld.assumeByClosingWorld(self.tms.assumedPolicies,
                                                 self.tms.assumedURIs,
                                                 self.tms.assumedStrings,
                                                 self.tms.assumedClosedWorlds)
                self.tms.assumedClosedWorlds.append(closedWorld)
                altSupport = [closedWorld]
#                desc = self.altDescriptions
            else:
                altSupport = []
#                desc = [x.substitution(env.asDict()) for x in self.descriptions]

            for r in result:
                # Do any substitution and then extract the description
                # and r12 from the particular r's tuple.
                r12 = r.substitution(env.asDict())
                prompt = r12[2]
                desc = r12[1]
                r12 = r12[0]
                
                r2 = r12.pattern
                support = r12.support
                ruleId = r12.rule
                assert isinstance(r2, Rule) or isinstance(r2, ImportedRuleset) or not r2.occurringIn(self.vars), (r2, env, penalty, self.label)
#            print '   ...... so about to assert %s' % r2
                # Justify r2's TMS node with the triples in
                # triplesTMS, and us as a rule (and any closed world)
                r2TMS = self.tms.getThingWithEnv(r2, env)
                if support is None:
                    if isinstance(r2, Rule):
                        r2TMS.justify(RuleName(self.sourceNode, desc, prompt), triplesTMS + [self.tms.getThing(self)] + altSupport)
                    elif isinstance(r2, ImportedRuleset):
                        r2TMS.justify(RuleName(self.sourceNode, desc, prompt), triplesTMS + [self.tms.getThing(self)] + altSupport)
                    else:
                        # Delay the justification of assertions in else clauses.
                        eventLoop.addAssertion(lambda: r2TMS.justify(RuleName(self.sourceNode, desc, prompt), triplesTMS + [self.tms.getThing(self)] + altSupport))
                else:
                    supportTMS = reduce(frozenset.union, support, frozenset()).union(altSupport)
                    if isinstance(r2, Rule):
                        r2TMS.justify(RuleName(ruleId, desc, prompt), supportTMS)
                    elif isinstance(r2, ImportedRuleset):
                        r2TMS.justify(RuleName(ruleId, desc, prompt), supportTMS)
                    else:
                        eventLoop.addAssertion(lambda: r2TMS.justify(RuleName(ruleId, desc, prompt), supportTMS))
#                assert self.tms.getThing(self).supported
#                assert r2TMS.supported

class ImportedRuleset(object):
    """An ImportedRuleset indicates a ruleset that should be imported from
    a remote URI."""
    def __init__(self, eventLoop, tms, goalWildcards, uri):
        self.eventLoop = eventLoop
        self.tms = tms
        self.goalWildcards = goalWildcards
        self.uri = uri

    def substitution(self, env):
        if not env:
            return self
        # TODO: Fix the goal direction with imported rulesets.
        return self.__class__(self.eventLoop, self.tms, self.goalWildcards,
                              self.uri.substitution(env))

class Rule(object):
    """A Rule contains all of the information necessary to build the rete
for a rule, and to handle when the rule fires. It does not care
much how the rule was represented in the rdf network
"""

    baseRules = set()
    
    def __init__(self, eventLoop, tms, vars, label,
                 pattern, contextFormula, result, alt, sourceNode,
                 goal=False, matchName=None, base=False, elided=False,
                 generated=False, goalWildcards={}, goalEnvironment=None):
        self.generatedLabel = False
        if label is None or label=='None':
            self.generatedLabel = True
            if not goal:
                label = '[pattern=%s]' % pattern
            else:
                label= '[goal=%s]' % pattern
        self.label = label
        self.eventLoop = eventLoop
        self.success = False
        self.tms = tms
        self.vars = vars | pattern.existentials()
        self.pattern = pattern
        self.patternToCompare = frozenset([x.spo() for x in pattern])
        self.contextFormula = contextFormula
        self.result = result
#        self.descriptions = descriptions
        self.alt = alt
#        assert isinstance(altDescriptions, list), altDescriptions
#        self.altDescriptions = altDescriptions
        self.goal = goal
        self.matchName = matchName
        self.sourceNode = sourceNode
        self.generated = generated
        self.isBase = base
        self.isElided = elided
        self.goalWildcards = goalWildcards
        self.goalEnvironments = set()
        # goalEnvironment is the environment in which reachable goals
        # should be evaluated.
        self.discoverReachableGoals(goalEnvironment)
        self.reachedGoal = False
        if base:
            self.baseRules.add(sourceNode)
        if debugLevel > 15:        
            print '''just made a rule, with
        tms=%s,
        vars=%s
        label=%s
        pattern=%s
        result=%s
        alt=%s
        matchName=%s''' % (tms, self.vars, label, pattern, result, alt, matchName)

    def discoverReachableGoals(self, goalEnvironment=None):
        """Find all goals reachable from this rule and set
        self.reachableGoals, so that these goals may be matched against to
        determine when the actual Rete of this rule should be built."""
        # We now have THIS rule, but not the goal rules it should be
        # contingent on.

        # Find all of the goals of this rule.
        goalFilter = set()

        # Find all goals this rule may ultimately match and add
        # them to the goals to use as a trigger.

        # Collect unbound vars with each result
        seenStatements = set()
        possibleResults = [(possibleResult, self.vars)
                           for possibleResult in self.result + self.alt]
        while len(possibleResults) > 0:
            possibleResult, vars = possibleResults.pop()
            if isinstance(possibleResult[0].pattern, Rule):
                # We will need to traverse descendants.
                possibleResults.extend([(newResult, possibleResult[0].pattern.vars)
                                        for newResult in possibleResult[0].pattern.result + possibleResult[0].pattern.alt])
            elif isinstance(possibleResult[0].pattern, ImportedRuleset):
                # We cannot predict whether this is useful or not
                # without doing an import, so assert a wildcard.
                goalFilter.add(StoredStatement((None,
                                                self.goalWildcards[PRED],
                                                self.goalWildcards[SUBJ],
                                                self.goalWildcards[OBJ])))
            else:  # isinstance(possibleResult[0].pattern, Formula)
                # We have an assertion formula.  Statements in it
                # are reachable goals.
                for statement in possibleResult[0].pattern.statements:
                    # Eugh.  hash(StoredStatement) is by id, not contents.
                    if statement.spo() not in seenStatements:
                        goalFilter.add(statement)
                        seenStatements.add(statement.spo())
        # POST: goalFilter now contains a set of all goal-patterns
        # (with variables set to None) reachable from this rule.
        if goalEnvironment is not None:
            # This is a rule generated by doing a substitution, so we
            # can substitute in the goals.
            self.reachableGoals = [statement.substitution(goalEnvironment)
                                   for statement in goalFilter]
        else:
            self.reachableGoals = goalFilter

    def __eq__(self, other):
        return isinstance(other, Rule) and \
               self.eventLoop is other.eventLoop and \
               self.tms is other.tms and \
               self.goal == other.goal and \
               self.vars == other.vars and \
               self.patternToCompare == other.patternToCompare and \
               self.result == other.result and \
               self.alt == other.alt and \
               self.matchName == other.matchName

    def __hash__(self):
        assert not isinstance(Rule, list)
        assert not isinstance(self.eventLoop, list)
        assert not isinstance(self.tms, list)
        assert not isinstance(self.vars, list)
        assert not isinstance(self.pattern, list)
        assert not isinstance(self.sourceNode, list)
        assert not isinstance(self.goal, list)
        assert not isinstance(self.matchName, list)
        return hash((Rule, self.eventLoop, self.tms, self.vars, self.pattern, self.sourceNode, self.goal, self.matchName))

    def __repr__(self):
        return '%s with vars %s' % (self.label.encode('utf_8'), self.vars)

    def compileToRete(self):
        """Compile the GOAL Rete (i.e. which matches the goals which can be
        reached from this rule and compiles the real Rete only when at
        least one of those goals exists.).

        Also builds a Rete which looks for when a goal is reached so
        as to turn off the main Rete and no longer process when a goal
        is reached.  Note that the main Rete will only ever consider
        its goal reached if all air:assert patterns that can be
        reached from this rule assert patterns of the form {A B C .}
        where A and C are the same for all assertions and B is a) the
        same for all assertions or b) a mixture of air:compliant-with
        or air:non-compliant-with for all assertions)."""
        patterns = self.pattern.statements
        if self.goal:
            workingContext = self.tms.getContext(GOAL)
        else:
            workingContext = self.tms.workingContext
        index = workingContext._index
#        buildGoals = False  # If true, goals will be matched first.
        self.goalBottomBeta = MM.compileGoalPattern(self.tms.getContext(GOAL)._index, patterns, self.vars, self.goalWildcards, self.tms.getContext(GOAL), self.reachableGoals, supportBuiltin=self.supportBuiltin)
        goalBottom = MM.ProductionNode(self.goalBottomBeta, self.assertNewGoals, lambda: True)

        # We also want to pattern match to see if a) there is only one
        # reachable goal (compliant-with & non-compliant-with together
        # count as a single reachable goal) and b) the reachable goal
        # is fully bound.

        # compliant-with/non-compliant-with is a kludge since we don't
        # actually do propositions like we should.
        reachableGoalCount = len(self.reachableGoals)
        if len(self.reachableGoals) == 2:
            (goal1, goal2) = list(self.reachableGoals)

            # Build the compliancePredicates relative to the relevant store!
            compliancePredicates = ['http://dig.csail.mit.edu/TAMI/2007/amord/air#compliant-with', 'http://dig.csail.mit.edu/TAMI/2007/amord/air#non-compliant-with']
            compliancePredicates = [goal1[PRED].store.newSymbol(pred) for pred in compliancePredicates]

            if goal1[SUBJ] == goal2[SUBJ] and goal1[OBJ] == goal2[OBJ] and goal1[PRED] in compliancePredicates and goal2[PRED] in compliancePredicates:
                reachableGoalCount = 1

        if reachableGoalCount == 1:
            # Check to make sure that everything is bound.
            foundUnboundVar = False
            for goal in self.reachableGoals:
                for term in goal:
                    if term in self.vars:
                        # Not fully bound!
                        foundUnboundVar = True
                        break
                if foundUnboundVar:
                    break
            
            if not foundUnboundVar:
                # No unbound variables left, so we can build the "off
                # switch"
                bottomBeta = MM.compileGoalPattern(self.tms.workingContext._index, self.reachableGoals, self.vars, self.goalWildcards, self.tms.workingContext, self.reachableGoals, supportBuiltin=self.supportBuiltin)
                trueBottom =  MM.ProductionNode(bottomBeta, self.onReachedGoal, lambda: True)

    def supportBuiltin(self, triple):
        """Called by the compiled Rete to support a built-in function's triple
        in the associated formulaTMS."""
        # Create the TMS node representing this triple's extraction.
        self.tms.getTriple(*triple.spo()).assumeBuiltin()
    
    def addTriple(self, triple):
        self.tms.getTriple(*triple.spo()).assume()
    def retractTriple(self, triple):
        self.tms.getTriple(*triple.spo()).retract()

    def assertNewGoals(self, (triples, environment, penalty)):
        """Called when a goal exists (by the GOAL Rete) to create the real
        rete based on what matched."""
        # Assert new goals of this rule by substituting in the matched
        # environments of goals this rule can satisfy.
        if environment not in self.goalEnvironments:
            # Only ever assert goals for a given environment once.
            self.goalEnvironments.add(environment)

            substituted_self = self.substitution(environment.asDict())
            patterns = substituted_self.pattern.statements

            # NOTE: Because this is a new Rule object, it has yet to
            # be supported in the TMS.  We must justify it identically
            # to how we justified this rule, before any conclusions
            # will also be supported.
            baseTMS = self.tms.getThing(self)
            substitutedTMS = self.tms.getThing(substituted_self)
            # We support this substituted rule contingent ONLY on the
            # original rule.
            substitutedTMS.justify(RuleName(self.sourceNode, '', ''), [baseTMS])

            if substituted_self.goal:
                workingContext = substituted_self.tms.getContext(GOAL)
            else:
                workingContext = substituted_self.tms.workingContext
            index = workingContext._index
            for triple in patterns:
                triple = triple.substitution(environment)
                # p cannot be a built-in (because built-ins are
                # inherently asserted by something other than a rule)
                if isinstance(triple[PRED], BuiltIn):
                    continue
                (s, p, o), newVars = canonicalizeVariables(triple, substituted_self.vars)
                substituted_self.eventLoop.addAssertion(AuxTripleJustifier(substituted_self.tms, GOAL, s, p, o, newVars, substituted_self.sourceNode, [substituted_self.tms.getThing(substituted_self)]))

            # TODO: Pass the environment.
            # TODO: This isn't triggering for the right things.

            # Do a substitution before compiling this rete.  We'll
            # build more retes but probably do less work with
            # recursive rules!
            def reallyCompileRete(eventLoop):
#                print "really compile", patterns

                bottomBeta = MM.compilePattern(index, patterns, substituted_self.vars, substituted_self.contextFormula, supportBuiltin=substituted_self.supportBuiltin, reachedGoal=lambda: substituted_self.reachedGoal)
                trueBottom =  MM.ProductionNode(bottomBeta, substituted_self.onSuccess, substituted_self.onFailure)
#            print "push", reallyCompileRete, "from", patterns
            self.eventLoop.pushPostGoal(reallyCompileRete)

    def onSuccess(self, (triples, environment, penalty)):
        """Add the success (air:then) event to the event loop to be fired at
        the next available opportunity."""
        event = RuleFire(self, triples, environment, penalty, self.result)
#        print "succeeded", self.pattern.statements, event
        self.success = True
        self.eventLoop.add(event)

    def onFailure(self):
        """Add the failure (air:else) event to the event loop to be fired at
        the next available opportunity."""
        assert not self.success
        if self.alt:
            event = RuleFire(self, [], Env(), 0, self.alt, alt=True)
#            print "failed", self.pattern.statements, event
            self.eventLoop.addAlternate(event)

    def onReachedGoal(self, (triples, environment, penalty)):
        """Called when a goal of this rule is reached to force the
        disconnection of the main Rete. (c.f. compileToRete)"""
        self.reachedGoal = True

    def substitution(self, env):
        """Called to substitute variables in this Rule."""
        if not env:
            return self
        pattern = self.pattern.substitution(env)
        result = [x.substitution(env) for x in self.result]
        alt = [x.substitution(env) for x in self.alt]
#        descriptions = [x.substitution(env) for x in self.descriptions]
#        altDescriptions = [x.substitution(env) for x in self.altDescriptions]
        if self.generatedLabel:
            label = None
        else:
            label = self.label
        return self.__class__(self.eventLoop, self.tms, self.vars,
                              label, pattern, self.contextFormula, result, alt,
                              self.sourceNode, self.goal, self.matchName, base=self.isBase, elided=self.isElided, generated=True, goalWildcards=self.goalWildcards, goalEnvironment=env)

    @classmethod
    def compileFromTriples(cls, eventLoop, tms, F, ruleNode, goal=False,
                           vars=frozenset(), preboundVars=frozenset(),
                           base=False, goalWildcards={}):
        """Compiles a Rule object which makes use of the specified eventLoop
        and TMS.  The rule is identified by the RDF node ruleNode
        contained in the Formula F.  If goal is True, the Rule is to
        be considered a goal-rule.  vars contains the set of defined
        variables (both bound and unbound) prior to the execution of
        this rule, while preboundVars contains only the set of bound
        variables.  If base is True, the rule being compiled is from
        the base AIR rule set (which contains basic RDFS and OWL
        rules); output of base rules are hidden when justifications
        are constructed.

        NOTE: This function does NOT compile the Rete tree for the
        Rule.  That is done by Rule.compileToRete."""

        # Define namespaces.
        assert tms is not None
        rdfs = F.newSymbol('http://www.w3.org/2000/01/rdf-schema')
        rdf = F.newSymbol('http://www.w3.org/1999/02/22-rdf-syntax-ns')
        p = F.newSymbol('http://dig.csail.mit.edu/TAMI/2007/amord/air')

        # Get the pattern for the rule (we'll need it for testing
        # which variables are bound in this rule)
        try:
            pattern = F.the(subj=ruleNode, pred=p['if'])
        except AssertionError:
            raise ValueError('%s has too many air:if clauses, being all of %s'
                             % (ruleNode, F.each(subj=ruleNode, pred=p['if'])))
        if pattern is None:
            raise ValueError('%s must have an air:if clause. You did not give it one' % (ruleNode,))
        
        # Find the variables used in this rule and determine if any
        # variables are newly bound when matching the pattern.
        vars = vars.union(F.universals())
        # ASSUMPTION: variables will never be a predicate(!)
        varsUsed = set([var for var in vars
                        if pattern.contains(subj=var) or pattern.contains(obj=var)])
        varBinding = len(varsUsed - preboundVars) > 0
        preboundVars = preboundVars.union(F.universals())

        realNode = ruleNode
        nodes = [realNode]

        # Get the air:then and air:else nodes.
        thenNodes = F.each(subj=ruleNode, pred=p['then'])
        elseNodes = F.each(subj=ruleNode, pred=p['else'])
        if varBinding and len(elseNodes) > 0:
            raise ValueError('%s has an air:else clause even though a variable is bound in its air:if, which is unsupported (did you mean to use @forSome instead of @forAll?)'
                             % (ruleNode))
#        if altNode:
#            nodes.append(altNode)
#            altDescriptions = list(F.each(subj=altNode, pred=p['description']))
#        else:
#            altDescriptions = []

        # Get the air:label value.
        # TODO: get this annotated on the ontology.
        label = F.the(subj=ruleNode, pred=p['label'])
        
        # Is the rule an air:Hidden-rule?
        base = base or (F.contains(subj=ruleNode, pred=F.store.type, obj=p['Hidden-rule']) == 1)
        # air:Elided-rule?
        elided = (F.contains(subj=ruleNode, pred=F.store.type, obj=p['Elided-rule']) == 1)
#        descriptions = list(F.each(subj=node, pred=p['description']))

        # Collect all air:then or air:else actions...
        resultList  = []
        
        # For each air:then node...
        subrules = []
        goal_subrules = []
        assertions = []
        goal_assertions = []
        import_rules = []
        for node in thenNodes:
            actions = []
            
            # Get any description...
            try:
                description = F.the(subj=node, pred=p['description'])
                if description == None:
                    description = SubstitutingList()
                else:
                    description = SubstitutingList([description])
            except AssertionError:
                raise ValueError('%s has too many descriptions in an air:then, being all of %s'
                                 % (ruleNode, F.each(subj=node, pred=p['description'])))
            try:
                prompt = F.the(subj=node, pred=p['prompt'])
                if prompt == None:
                    prompt = SubstitutingList()
                else:
                    prompt = SubstitutingList([prompt])
            except AssertionError:
                raise ValueError('%s has too many prompts in an air:then, being all of %s'
                                 % (ruleNode, F.each(subj=node, pred=p['prompt'])))
            
            # Get any subrule...
            subrule = None
            try:
                subruleNode = F.the(subj=node, pred=p['rule'])
                if subruleNode is not None:
                    subrule = Assertion(cls.compileFromTriples(eventLoop, tms, F, subruleNode, vars=vars, preboundVars=preboundVars, base=base, goalWildcards=goalWildcards))
            except AssertionError:
                raise ValueError('%s has too many rules in an air:then, being all of %s'
                                 % (ruleNode, F.each(subj=node, pred=p['rule'])))
            if subrule is not None:
                subrules.append(SubstitutingTuple((subrule, description, prompt)))
                actions.append(subrule)
            
            # Get any goal-subrule...
            goal_subrule = None
            try:
                goal_subruleNode = F.the(subj=node, pred=p['goal-rule'])
                if goal_subruleNode is not None:
                    goal_subrule = Assertion(cls.compileFromTriples(eventLoop, tms, F, goal_subruleNode, goal=True, vars=vars, preboundVars=preboundVars, base=base, goalWildcards=goalWildcards))
            except AssertionError:
                raise ValueError('%s has too many goal-rules in an air:then, being all of %s'
                                 % (ruleNode, F.each(subj=node, pred=p['goal-rule'])))
            if goal_subrule is not None:
                goal_subrules.append(
                    SubstitutingTuple((goal_subrule, description, prompt)))
                actions.append(goal_subrule)
            
            # Get any assertion...
            try:
                assertion = F.the(subj=node, pred=p['assert'])
            except AssertionError:
                raise ValueError('%s has too many assertions in an air:then, being all of %s'
                                 % (ruleNode, F.each(subj=node, pred=p['assert'])))
            if assertion is not None:
                assertions.append(SubstitutingTuple((assertion, description, prompt)))
                actions.append(assertion)
            
            # Get any goal-assertion...
            try:
                goal_assertion = F.the(subj=node, pred=p['assert-goal'])
            except AssertionError:
                raise ValueError('%s has too many goal-assertions in an air:then, being all of %s'
                                 % (ruleNode, F.each(subj=node, pred=p['assert-goal'])))
            if goal_assertion is not None:
                goal_assertions.append(
                    SubstitutingTuple((goal_assertion, description, prompt)))
                actions.append(goal_assertion)
            
            # TODO: Actually add some explanation here...
            # Get any import...
            try:
                import_rule = F.the(subj=node, pred=p['import'])
                if import_rule is not None:
                    import_rule = Assertion(ImportedRuleset(eventLoop, tms, goalWildcards, import_rule))
            except AssertionError:
                raise ValueError('%s has too many imports in an air:then, being all of %s'
                                 % (ruleNode, F.each(subj=node, pred=p['import'])))
            if import_rule is not None:
                import_rules.append(
                    SubstitutingTuple((import_rule, description, prompt)))
                actions.append(import_rule)
            
            # Make sure there was exactly one of the above.
            if len(actions) != 1:
                raise ValueError('%s has more than one of {air:rule, air:goal-rule, air:assert, air:assert-goal, air:import} in an air:then, being all of %s'
                                 % (ruleNode, actions))
            
        # Get the data from the assertions.
        assertionObjs = []
        for assertion in assertions + goal_assertions:
            prompt = assertion[2]
            description = assertion[1]
            statement = assertion[0]
            if F.any(subj=statement, pred=p['statement']) is not None:
                if ruleNode not in airstatementWarnings:
                    print "WARNING: %s has an air:statement clause inside an air:assert clause.  This is no longer supported in AIR 2.5, and will not work with future versions of the reasoner." % (ruleNode)
                    airstatementWarnings.add(ruleNode)
                statement = F.the(subj=statement, pred=p['statement'])
            assertionObjs.append(SubstitutingTuple(
                    (Assertion(statement),
                     description,
                     prompt)))
        resultList.append(subrules + assertionObjs + goal_subrules + import_rules)
        
        # Now do what we did to collect the assertions and such for
        # any air:else actions.
        subrules = []
        goal_subrules = []
        assertions = []
        goal_assertions = []
        import_rules = []
        for node in elseNodes:
            actions = []
            
            # Get any description...
            try:
                description = F.the(subj=node, pred=p['description'])
                if description == None:
                    description = SubstitutingList()
                else:
                    description = SubstitutingList([description])
            except AssertionError:
                raise ValueError('%s has too many descriptions in an air:else, being all of %s'
                                 % (ruleNode, F.each(subj=node, pred=p['description'])))
            try:
                prompt = F.the(subj=node, pred=p['prompt'])
                if prompt == None:
                    prompt = SubstitutingList()
                else:
                    prompt = SubstitutingList([prompt])
            except AssertionError:
                raise ValueError('%s has too many prompts in an air:else, being all of %s'
                                 % (ruleNode, F.each(subj=node, pred=p['prompt'])))

            # Get any subrule...
            subrule = None
            try:
                subruleNode = F.the(subj=node, pred=p['rule'])
                if subruleNode is not None:
                    subrule = Assertion(cls.compileFromTriples(eventLoop, tms, F, subruleNode, vars=vars, preboundVars=preboundVars, base=base, goalWildcards=goalWildcards))
            except AssertionError:
                raise ValueError('%s has too many rules in an air:else, being all of %s'
                                 % (ruleNode, F.each(subj=node, pred=p['rule'])))
            if subrule is not None:
                subrules.append(SubstitutingTuple((subrule, description, prompt)))
                actions.append(subrule)
            
            # Get any goal-subrule...
            goal_subrule = None
            try:
                goal_subruleNode = F.the(subj=node, pred=p['goal-rule'])
                if goal_subruleNode is not None:
                    goal_subrule = Assertion(cls.compileFromTriples(eventLoop, tms, F, goal_subruleNode, goal=True, vars=vars, preboundVars=preboundVars, base=base, goalWildcards=goalWildcards))
            except AssertionError:
                raise ValueError('%s has too many goal-rules in an air:else, being all of %s'
                                 % (ruleNode, F.each(subj=node, pred=p['goal-rule'])))
            if goal_subrule is not None:
                goal_subrules.append(
                    SubstitutingTuple((goal_subrule, description, prompt)))
                actions.append(goal_subrule)
            
            # Get any assertion...
            try:
                assertion = F.the(subj=node, pred=p['assert'])
            except AssertionError:
                raise ValueError('%s has too many assertions in an air:else, being all of %s'
                                 % (ruleNode, F.each(subj=node, pred=p['assert'])))
            if assertion is not None:
                assertions.append(SubstitutingTuple((assertion, description, prompt)))
                actions.append(assertion)
            
            # Get any goal-assertion...
            try:
                goal_assertion = F.the(subj=node, pred=p['assert-goal'])
            except AssertionError:
                raise ValueError('%s has too many goal-assertions in an air:else, being all of %s'
                                 % (ruleNode, F.each(subj=node, pred=p['assert-goal'])))
            if goal_assertion is not None:
                goal_assertions.append(
                    SubstitutingTuple((goal_assertion, description, prompt)))
                actions.append(goal_assertion)
            
            # TODO: Actually add some explanation here...
            # Get any import...
            try:
                import_rule = F.the(subj=node, pred=p['import'])
                if import_rule is not None:
                    import_rule = Assertion(ImportedRuleset(eventLoop, tms, goalWildcards, import_rule))
            except AssertionError:
                raise ValueError('%s has too many imports in an air:else, being all of %s'
                                 % (ruleNode, F.each(subj=node, pred=p['import'])))
            if import_rule is not None:
                import_rules.append(
                    SubstitutingTuple((import_rule, description, prompt)))
                actions.append(import_rule)
            
            # Make sure there was exactly one of the above.
            if len(actions) != 1:
                raise ValueError('%s has more than one of {air:rule, air:goal-rule, air:assert, air:assert-goal, air:import} in an air:else, being all of %s'
                                 % (ruleNode, actions))
            
        # Get the data from the assertions.
        assertionObjs = []
        for assertion in assertions + goal_assertions:
            prompt = assertion[2]
            description = assertion[1]
            statement = assertion[0]
            if F.any(subj=statement, pred=p['statement']) is not None:
                if ruleNode not in airstatementWarnings:
                    print "WARNING: %s has an air:statement clause inside an air:assert clause.  This is no longer supported in AIR 2.5, and will not work with future versions of the reasoner." % (ruleNode)
                    airstatementWarnings.add(ruleNode)
                statement = F.the(subj=statement, pred=p['statement'])
            assertionObjs.append(SubstitutingTuple(
                    (Assertion(statement),
                     description,
                     prompt)))
        resultList.append(subrules + assertionObjs + goal_subrules + import_rules)
        
        node = ruleNode
        matchedGraph = F.the(subj=node, pred=p['matched-graph'])
        
        # Construct the rule object.
        self = cls(eventLoop, tms,
                   vars, unicode(label),
                   pattern,
                   F,
                   resultList[0],
#                   descriptions=descriptions,
                   alt=resultList[1],# altDescriptions=altDescriptions,
                   goal=goal, matchName=matchedGraph, sourceNode=node, base=base, elided=elided, goalWildcards=goalWildcards)
        return self

    @classmethod
    def compileCwmRule(cls, eventLoop, tms, F, triple):
        """Compile an old-form cwm rule (log:implies)"""
        assert tms is not None
        label = "Rule from cwm with pattern %s" % triple.subject()
        pattern = triple.subject()
        assertions = [(Assertion(triple.object()), None)]
        vars = frozenset(F.universals())
        self = cls(eventLoop, tms,
                   vars, unicode(label),
                   pattern,
                   assertions,
                   alt=[],
                   goal=False,
                   matchName=None,
                   sourceNode=pattern,
                   base=True,
                   elided=False)
        return self


    @classmethod
    def compileFormula(cls, eventLoop, formulaTMS, pf, base=False, goalWildcards={}):
        """Compile all rules in a formula."""
        rdf = pf.newSymbol('http://www.w3.org/1999/02/22-rdf-syntax-ns')
        p = pf.newSymbol('http://dig.csail.mit.edu/TAMI/2007/amord/air')
        # New AIR terminology.
        policies = pf.each(pred=rdf['type'], obj=p['RuleSet'])
        policies += pf.each(pred=rdf['type'], obj=p['Policy'])
#        globalVars = frozenset(pf.each(pred=rdf['type'], obj=p['Variable']))
        globalVars = frozenset(pf.universals())
        cwm_rules = [cls.compileCwmRule(eventLoop,
                                        formulaTMS,
                                        pf,
                                        x)
                     for x in pf.statementsMatching(pred=pf.store.implies)]
        rules = reduce(list.__add__, [[cls.compileFromTriples(eventLoop,
                                        formulaTMS,
                                        pf,
                                        x,
#                                        vars=globalVars.union(pf.each(subj=y, pred=p['variable'])),
                                        vars=globalVars,
                                        base=base,
                                        goalWildcards=goalWildcards)
                        for x in pf.each(subj=y, pred=p['rule'])]
                    for y in policies], [])
        goal_rules = reduce(list.__add__, [[cls.compileFromTriples(eventLoop,
                                                       formulaTMS,
                                                       pf,
                                                       x,
#                                                       vars=globalVars.union(pf.each(subj=y, pred=p['variable'])),
                                                       vars=globalVars,
                                                       base=base,
                                                       goalWildcards=goalWildcards)
                        for x in pf.each(subj=y, pred=p['goal-rule'])]
                    for y in policies], [])
        return policies, rules, goal_rules, cwm_rules               





uriGenCount = [0]
def nameRules(pf, uriBase):
    rdf = pf.newSymbol('http://www.w3.org/1999/02/22-rdf-syntax-ns')
    p = pf.newSymbol('http://dig.csail.mit.edu/TAMI/2007/amord/air')
    bindings = {}
    for statement in chain(pf.statementsMatching(pred=p['rule']),
                                        pf.statementsMatching(pred=['goal-rule'])):
        node = statement.subject()
        if node in pf.existentials():
            bindings[node] = uriBase + str(uriGenCount[0])
            uriGenCount[0] += 1
    return pf.substitution(bindings)


class EventLoop(object):
    """The eventloop (there should only be one)
is a FIFO of thunks to be called.

Note that this eventloop support altevents (for else clauses) which
fire only when there are no events to fire.

The order of phases is as follows:

1. The eventloop is initially OPEN.
2. When all positive events have been exhausted, the eventloop is
CLOSED and all alternate events are run.  Any assertions made during
the alternate event SHOULD be placed on the assertion queue (to be
evaluated after all alternate events have run).  Any alternate events
made during the CLOSED phase should be placed on a "next alternate
event queue" to be run after the eventloop has had a chance to enter
the OPEN state at least once.
3. When all alternate events have been exhausted, the eventloop is
REOPENing and all assertion events asserted as a result of the
alternate events are run.
4. If no positive, alternate, or assertion events remain, post-goal
events are run (i.e. creating retes) and the world is officially OPEN.
5. When the loop shifts from the CLOSED state to a REOPEN or an OPEN
state, any alternate events queued in the "next alternate event queue"
during the CLOSED state are moved to the normal alternate event queue.
"""
    PHASE_OPEN = 0
    PHASE_CLOSED = 1
    PHASE_REOPEN = 2
    
    def __init__(self):
        self.events = deque()
        self.alternateEvents = deque()
        self.postGoalEvents = deque()
        self.phase = EventLoop.PHASE_OPEN
        self.assertionEvents = deque()
        self.newAlternateEvents = deque()

    def add(self, event):
        """Add an event to be called during the OPEN phase of the event
        loop."""
#        if hasattr(event, 'rule'):
#            print "add", event.rule
#        print "add", event
        self.events.appendleft(event)

    def addAlternate(self, event):
        """Add an event to be called during the CLOSED phase of the event
        loop.  If the eventloop is CLOSED, the event is added to a
        "next alternate event" queue so that the event loop will get a
        chance to return to the OPEN state before those alternate
        events are added."""
#        print "addAlternate", event.rule
#        print "addAlternate", event
        if self.phase != EventLoop.PHASE_CLOSED:
            self.alternateEvents.appendleft(event)
        else:
            self.newAlternateEvents.appendleft(event)

    def pushPostGoal(self, event):
        """Add an event to run as a result of matching a goal (i.e. compiling
        a Rete tree for a rule).  This will only be run when the loop
        is in the REOPEN phase."""
        # Only ever run this event once we're done with normal
        # events. (i.e. once all necessary goal-rules have been
        # matched.)

        # Also, this runs as a stack.  Goals matched later will
        # execute first.
#        print "postGoal", event
        self.postGoalEvents.append(event)
    
    def addAssertion(self, event):
        """Make an assertion.  If the eventloop is not in the OPEN phase, the
        assertion event is queued until such time as it is OPEN again."""
#        print "addAssertion", event
        if self.phase != EventLoop.PHASE_OPEN:
            self.assertionEvents.appendleft(event)
        else:
            event()

    def next(self):
        # Order of events is as follows:
        #
        # 1. Exhaust all possible open world events (successes)
        # 2. Close the world and exhaust all EXISTING alternative events (failures) while queuing new ones.
        # 3. Exhaust all assertion events resulting from the above.
        # 4. Build no more than one Rete tree (the least recently requested build[?]  I'm not sure why it's least rather than most, but sure.).
        # 5. If a Rete tree was built, goto 1.
        # 6. If any open world events are newly pending, goto 1.
        # 7. If any alternative events are newly pending, goto 2.
        if self.phase == EventLoop.PHASE_OPEN and self.events:
            event = self.events.pop()
#            print "open event", event
            return event(self)
        elif self.phase <= EventLoop.PHASE_CLOSED and self.alternateEvents:
            event = self.alternateEvents.pop()
#            print "closed event", event
            self.phase = EventLoop.PHASE_CLOSED
            return event(self)
        elif self.phase <= EventLoop.PHASE_REOPEN and self.assertionEvents:
            self.phase = EventLoop.PHASE_REOPEN
            if len(self.newAlternateEvents) > 0:
                self.alternateEvents = self.newAlternateEvents
                self.newAlternateEvents = deque()
            event = self.assertionEvents.pop()
#            print "reopening event", event
            return event()
        elif self.phase <= EventLoop.PHASE_REOPEN and self.postGoalEvents:
            self.phase = EventLoop.PHASE_OPEN
            if len(self.newAlternateEvents) > 0:
                self.alternateEvents = self.newAlternateEvents
                self.newAlternateEvents = deque()
            event = self.postGoalEvents.popleft()
#            print "build", event
            return event(self)
        elif self.events:
            self.phase = EventLoop.PHASE_OPEN
            if len(self.newAlternateEvents) > 0:
                self.alternateEvents = self.newAlternateEvents
                self.newAlternateEvents = deque()
            event = self.events.pop()
#            print "force open event", event
            return event(self)
        elif self.alternateEvents:
            self.phase = EventLoop.PHASE_CLOSED
            event = self.alternateEvents.pop()
#            print "force closed event", event
            return event(self)
        else:
            if len(self.newAlternateEvents) > 0:
                self.alternateEvents = self.newAlternateEvents
                self.newAlternateEvents = deque()
            return None

    def __len__(self):
        return len(self.events) + len(self.alternateEvents) + len(self.assertionEvents) + len(self.postGoalEvents)


            

def setupTMS(store):
    """Create the working context and associate a formulaTMS (returned)
    with it."""
    workingContext = store.newFormula()
    workingContext.keepOpen = True
    formulaTMS = FormulaTMS(workingContext)
    return formulaTMS
    

def loadFactFormula(formulaTMS, uri, closureMode=""): #what to do about closureMode?
    """Load a fact formula from a URI and assume each triple in the
    formula by "extraction" in the TMS from that formula."""
##    We're not ready for this yet!
##    store = formulaTMS.workingContext.store
##    s = store.newSymbol(uri)
##    assert isinstance(s, Symbol)
##    formulaTMS.getThing(s).assume()
##    return s
    f = _loadF(formulaTMS, uri, closureMode)
    formulaTMS.getThing(f).assumeByExtraction(uri)
    formulaTMS.assumedURIs.append(formulaTMS.workingContext.newSymbol(uri))
    return f

def _loadF(formulaTMS, uri, closureMode=""):
    """Load and return a formula from a URI"""
    if loadFactFormula.pClosureMode:
        closureMode += "p"
    store = formulaTMS.workingContext.store
    f = store.newFormula()
    f.setClosureMode(closureMode)
    f = store.load(uri, openFormula=f)
    return f

def parseN3(store, f, string):
    """Parse an N3 file and return the formula."""
    import notation3
    p = notation3.SinkParser(store, f)

    p.startDoc()
    p.feed(string)
    f = p.endDoc()

    f = f.close()
    return f


def loadFactFormulaObj(formulaTMS, f, closureMode=""):
    """Assume the contents of a formula object in the TMS."""
    if loadFactFormula.pClosureMode:
        closureMode += "p"
    fCopy = store.newFormula()
    fCopy.setClosureMode(closureMode)
    fCopy.loadFormulaWithSubstitution(f, Env())
    formulaTMS.getThing(fCopy).assume()
    formulaTMS.assumedStrings.append(formulaTMS.workingContext.newLiteral(f.n3String(), dt=n3NS))
    return fCopy


def loadFactN3(formulaTMS, string, closureMode=""):
    """LOad the contents of a fact formula in the TMS from an N3 string."""
    if loadFactFormula.pClosureMode:
        closureMode += "p"
    store = formulaTMS.workingContext.store
    f = store.newFormula()
    f.setClosureMode(closureMode)    
    f = parseN3(store, f, string)
    formulaTMS.getThing(f).assumeByParsingN3(f)
    formulaTMS.assumedStrings.append(formulaTMS.workingContext.newLiteral(string, dt=n3NS))
    return f    

loadFactFormula.pClosureMode = False



baseFactsURI = 'http://dig.csail.mit.edu/TAMI/2007/amord/base-assumptions.ttl'
baseRulesURI = 'http://dig.csail.mit.edu/TAMI/2007/amord/base-rules.air_2_5.ttl'

#baseFactsURI =
#baseRulesURI = 'data:text/rdf+n3;charset=utf-8,' # quite empty

store = llyn.RDFStore()

n3NS = store.newSymbol('http://www.w3.org/2000/10/swap/grammar/n3#n3')

def makeRDFSRules(eventLoop, tms):
    """Create retes to search the tms for interesting RDFS/OWL ontology
    properties (e.g. owl:sameAs, rdfs:domain, etc.) and create
    appropriate non-goal-directed rules for any discovered RDFS/OWL
    statements.
    """
    class FakeRule(object):
        """A mock Rule object, required as RuleFire objects require something
        that looks like a Rule object."""
        pass

    # Make the new formula we're going to create patterns in.
    rdfsFormula = store.newFormula()
    rdf = rdfsFormula.newSymbol('http://www.w3.org/1999/02/22-rdf-syntax-ns')
    rdfs = rdfsFormula.newSymbol('http://www.w3.org/2000/01/rdf-schema')
    owl = rdfsFormula.newSymbol('http://www.w3.org/2002/07/owl')
    p = rdfsFormula.newSymbol('http://dig.csail.mit.edu/TAMI/2007/amord/air')

    # Variables (could be anything)
    fillerA = rdfsFormula.newUniversal(rdfsFormula.store.genId())
    fillerB = rdfsFormula.newUniversal(rdfsFormula.store.genId())
    fillerC = rdfsFormula.newUniversal(rdfsFormula.store.genId())
    vars = frozenset([fillerA, fillerB, fillerC])

    # Pattern match RDFS/OWL statements in the main context.
    index = tms.workingContext._index

    doNothing = lambda: 1
    def makePatternAssertionThunk(parentTriples, statement, description):
        """Make the pattern assertion thunk for use with a rete onsuccess
        call.  This thunk will create a RuleFire object, asserting the
        statements in the formula specified by the argument statement
        (with description).  triples in parentTriples will not count
        as matches (so that copies of the owl:sameAs statement will be
        ignored, for example).  sourceNode is a convenience for
        fakeRule, and should be the predicate responsible for this
        pattern assertion."""
        prompt = SubstitutingList()

        result = [
            SubstitutingTuple(
                (Assertion(statement),
                 description,
                 prompt))
            ]

        def assertPattern((triples, environment, penalty)):
            # Ignore the statement that generated this.
            if triples == parentTriples:
                return

            fakeRule = FakeRule()
            fakeRule.tms = tms
            fakeRule.matchName = None
            fakeRule.goal = True
            fakeRule.vars = frozenset()
            fakeRule.sourceNode = parentTriples[0][PRED]

            event = RuleFire(fakeRule, triples, environment, penalty, result)
            eventLoop.add(event)

        return assertPattern

    # owl:sameAs
    def buildOWLSameAs((triples, environment, penalty)):
        """Build the non-goal rules for an owl:sameAs property."""
        # Assert rules in both goals and non-goals.
        for context in (tms.getContext(GOAL), tms.workingContext):
            index = context._index

            # :subj :A :B -> :obj :A :B
            ifPattern = rdfsFormula.newFormula()
            ifPattern.add(environment[fillerA], fillerA, fillerB)
            ifPattern = ifPattern.close()

            statement = rdfsFormula.newFormula()
            statement.add(environment[fillerB], fillerA, fillerB)
            statement.close()
            description = SubstitutingList([rdfsFormula.newList([
                            environment[fillerA],
                            rdfsFormula.newLiteral(" is the same as "),
                            environment[fillerB]])])
            patternAssertionThunk = makePatternAssertionThunk(triples, statement, description)

            bottomBeta = MM.compilePattern(index, ifPattern.statements, vars, rdfsFormula, supportBuiltin=False, reachedGoal=lambda: False)
            trueBottom =  MM.ProductionNode(bottomBeta, patternAssertionThunk, doNothing)

            # :obj :A :B -> :subj :A :B
            ifPattern = rdfsFormula.newFormula()
            ifPattern.add(environment[fillerB], fillerA, fillerB)
            ifPattern = ifPattern.close()

            statement = rdfsFormula.newFormula()
            statement.add(environment[fillerA], fillerA, fillerB)
            statement.close()
            description = SubstitutingList([rdfsFormula.newList([
                            environment[fillerB],
                            rdfsFormula.newLiteral(" is the same as "),
                            environment[fillerA]])])
            patternAssertionThunk = makePatternAssertionThunk(triples, statement, description)

            bottomBeta = MM.compilePattern(index, ifPattern.statements, vars, rdfsFormula, supportBuiltin=False, reachedGoal=lambda: False)
            trueBottom =  MM.ProductionNode(bottomBeta, patternAssertionThunk, doNothing)

            # :A :subj :B -> :A :obj :B
            ifPattern = rdfsFormula.newFormula()
            ifPattern.add(fillerA, environment[fillerA], fillerB)
            ifPattern = ifPattern.close()

            statement = rdfsFormula.newFormula()
            statement.add(fillerA, environment[fillerB], fillerB)
            statement.close()
            description = SubstitutingList([rdfsFormula.newList([
                            environment[fillerA],
                            rdfsFormula.newLiteral(" is the same as "),
                            environment[fillerB]])])
            patternAssertionThunk = makePatternAssertionThunk(triples, statement, description)

            bottomBeta = MM.compilePattern(index, ifPattern.statements, vars, rdfsFormula, supportBuiltin=False, reachedGoal=lambda: False)
            trueBottom =  MM.ProductionNode(bottomBeta, patternAssertionThunk, doNothing)

            # :A :obj :B -> :A :subj :B
            ifPattern = rdfsFormula.newFormula()
            ifPattern.add(fillerA, environment[fillerB], fillerB)
            ifPattern = ifPattern.close()

            statement = rdfsFormula.newFormula()
            statement.add(fillerA, environment[fillerA], fillerB)
            statement.close()
            description = SubstitutingList([rdfsFormula.newList([
                            environment[fillerB],
                            rdfsFormula.newLiteral(" is the same as "),
                            environment[fillerA]])])
            patternAssertionThunk = makePatternAssertionThunk(triples, statement, description)

            bottomBeta = MM.compilePattern(index, ifPattern.statements, vars, rdfsFormula, supportBuiltin=False, reachedGoal=lambda: False)
            trueBottom =  MM.ProductionNode(bottomBeta, patternAssertionThunk, doNothing)

            # :A :B :subj -> :A :B :obj
            ifPattern = rdfsFormula.newFormula()
            ifPattern.add(fillerA, fillerB, environment[fillerA])
            ifPattern = ifPattern.close()

            statement = rdfsFormula.newFormula()
            statement.add(fillerA, fillerB, environment[fillerB])
            statement.close()
            description = SubstitutingList([rdfsFormula.newList([
                            environment[fillerA],
                            rdfsFormula.newLiteral(" is the same as "),
                            environment[fillerB]])])
            patternAssertionThunk = makePatternAssertionThunk(triples, statement, description)

            bottomBeta = MM.compilePattern(index, ifPattern.statements, vars, rdfsFormula, supportBuiltin=False, reachedGoal=lambda: False)
            trueBottom =  MM.ProductionNode(bottomBeta, patternAssertionThunk, doNothing)

            # :A :B :obj -> :A :B :subj
            ifPattern = rdfsFormula.newFormula()
            ifPattern.add(fillerA, fillerB, environment[fillerB])
            ifPattern = ifPattern.close()

            statement = rdfsFormula.newFormula()
            statement.add(fillerA, fillerB, environment[fillerA])
            statement.close()
            description = SubstitutingList([rdfsFormula.newList([
                            environment[fillerB],
                            rdfsFormula.newLiteral(" is the same as "),
                            environment[fillerA]])])
            patternAssertionThunk = makePatternAssertionThunk(triples, statement, description)

            bottomBeta = MM.compilePattern(index, ifPattern.statements, vars, rdfsFormula, supportBuiltin=False, reachedGoal=lambda: False)
            trueBottom =  MM.ProductionNode(bottomBeta, patternAssertionThunk, doNothing)

    # Search for owl:sameAs statements.
    ifPattern = rdfsFormula.newFormula()
    ifPattern.add(fillerA, owl['sameAs'], fillerB)
    ifPattern = ifPattern.close()
    bottomBeta = MM.compilePattern(index, ifPattern.statements, vars, rdfsFormula, supportBuiltin=False, reachedGoal=lambda: False)
    trueBottom =  MM.ProductionNode(bottomBeta, buildOWLSameAs, doNothing)

    # rdfs:domain
    def buildRDFSDomain((triples, environment, penalty)):
        """Build the non-goal rules for an rdfs:domain property."""
        # Assert rules in both goals and non-goals.
        for context in (tms.getContext(GOAL), tms.workingContext):
            index = context._index

            # :A :subj :B -> :A a :obj
            ifPattern = rdfsFormula.newFormula()
            ifPattern.add(fillerA, environment[fillerA], fillerB)
            ifPattern = ifPattern.close()

            statement = rdfsFormula.newFormula()
            statement.add(fillerA, rdf['type'], environment[fillerB])
            statement.close()
            description = SubstitutingList([rdfsFormula.newList([
                            environment[fillerA],
                            rdfsFormula.newLiteral(" has domain "),
                            environment[fillerB]])])
            patternAssertionThunk = makePatternAssertionThunk(triples, statement, description)

            bottomBeta = MM.compilePattern(index, ifPattern.statements, vars, rdfsFormula, supportBuiltin=False, reachedGoal=lambda: False)
            trueBottom =  MM.ProductionNode(bottomBeta, patternAssertionThunk, doNothing)

    # Search for rdfs:domain statements.
    ifPattern = rdfsFormula.newFormula()
    ifPattern.add(fillerA, rdfs['domain'], fillerB)
    ifPattern = ifPattern.close()
    bottomBeta = MM.compilePattern(index, ifPattern.statements, vars, rdfsFormula, supportBuiltin=False, reachedGoal=lambda: False)
    trueBottom =  MM.ProductionNode(bottomBeta, buildRDFSDomain, doNothing)

    # rdfs:range
    def buildRDFSRange((triples, environment, penalty)):
        """Build the non-goal rules for an rdfs:range property."""
        # Assert rules in both goals and non-goals.
        for context in (tms.getContext(GOAL), tms.workingContext):
            index = context._index

            # :A :subj :B -> :B a :obj
            ifPattern = rdfsFormula.newFormula()
            ifPattern.add(fillerA, environment[fillerA], fillerB)
            ifPattern = ifPattern.close()

            statement = rdfsFormula.newFormula()
            statement.add(fillerB, rdf['type'], environment[fillerB])
            statement.close()
            description = SubstitutingList([rdfsFormula.newList([
                            environment[fillerA],
                            rdfsFormula.newLiteral(" has range "),
                            environment[fillerB]])])
            patternAssertionThunk = makePatternAssertionThunk(triples, statement, description)

            bottomBeta = MM.compilePattern(index, ifPattern.statements, vars, rdfsFormula, supportBuiltin=False, reachedGoal=lambda: False)
            trueBottom =  MM.ProductionNode(bottomBeta, patternAssertionThunk, doNothing)

    # Search for rdfs:range statements.
    ifPattern = rdfsFormula.newFormula()
    ifPattern.add(fillerA, rdfs['range'], fillerB)
    ifPattern = ifPattern.close()
    bottomBeta = MM.compilePattern(index, ifPattern.statements, vars, rdfsFormula, supportBuiltin=False, reachedGoal=lambda: False)
    trueBottom =  MM.ProductionNode(bottomBeta, buildRDFSRange, doNothing)

    # rdfs:subClassOf
    def buildRDFSSubClassOf((triples, environment, penalty)):
        """Build the non-goal rules for an rdfs:subClassOf property."""
        # Assert rules in both goals and non-goals.
        for context in (tms.getContext(GOAL), tms.workingContext):
            index = context._index

            # :A a :subj -> :A a :obj
            ifPattern = rdfsFormula.newFormula()
            ifPattern.add(fillerA, rdf['type'], environment[fillerA])
            ifPattern = ifPattern.close()

            statement = rdfsFormula.newFormula()
            statement.add(fillerA, rdf['type'], environment[fillerB])
            statement.close()
            description = SubstitutingList([rdfsFormula.newList([
                            environment[fillerA],
                            rdfsFormula.newLiteral(" is a sub-class of "),
                            environment[fillerB]])])
            patternAssertionThunk = makePatternAssertionThunk(triples, statement, description)

            bottomBeta = MM.compilePattern(index, ifPattern.statements, vars, rdfsFormula, supportBuiltin=False, reachedGoal=lambda: False)
            trueBottom =  MM.ProductionNode(bottomBeta, patternAssertionThunk, doNothing)

    # Search for rdfs:subClassOf statements.
    ifPattern = rdfsFormula.newFormula()
    ifPattern.add(fillerA, rdfs['subClassOf'], fillerB)
    ifPattern = ifPattern.close()
    bottomBeta = MM.compilePattern(index, ifPattern.statements, vars, rdfsFormula, supportBuiltin=False, reachedGoal=lambda: False)
    trueBottom =  MM.ProductionNode(bottomBeta, buildRDFSSubClassOf, doNothing)

    # rdfs:subPropertyOf
    def buildRDFSSubPropertyOf((triples, environment, penalty)):
        """Build the non-goal rules for an rdfs:subPropertyOf property."""
        # Assert rules in both goals and non-goals.
        for context in (tms.getContext(GOAL), tms.workingContext):
            index = context._index

            # :A :subj :B -> :A :obj :B
            ifPattern = rdfsFormula.newFormula()
            ifPattern.add(fillerA, environment[fillerA], fillerB)
            ifPattern = ifPattern.close()

            statement = rdfsFormula.newFormula()
            statement.add(fillerA, environment[fillerB], fillerB)
            statement.close()
            description = SubstitutingList([rdfsFormula.newList([
                            environment[fillerA],
                            rdfsFormula.newLiteral(" is a sub-property of "),
                            environment[fillerB]])])
            patternAssertionThunk = makePatternAssertionThunk(triples, statement, description)

            bottomBeta = MM.compilePattern(index, ifPattern.statements, vars, rdfsFormula, supportBuiltin=False, reachedGoal=lambda: False)
            trueBottom =  MM.ProductionNode(bottomBeta, patternAssertionThunk, doNothing)

    # Search for rdfs:subPropertyOf statements.
    ifPattern = rdfsFormula.newFormula()
    ifPattern.add(fillerA, rdfs['subPropertyOf'], fillerB)
    ifPattern = ifPattern.close()
    bottomBeta = MM.compilePattern(index, ifPattern.statements, vars, rdfsFormula, supportBuiltin=False, reachedGoal=lambda: False)
    trueBottom =  MM.ProductionNode(bottomBeta, buildRDFSSubPropertyOf, doNothing)

    # owl:SymmetricProperty
    def buildOWLSymmetricProperty((triples, environment, penalty)):
        """Build the non-goal rules for an owl:SymmetricProperty property."""
        # Assert rules in both goals and non-goals.
        for context in (tms.getContext(GOAL), tms.workingContext):
            index = context._index

            # :A :subj :B -> :B :subj :A
            ifPattern = rdfsFormula.newFormula()
            ifPattern.add(fillerA, environment[fillerA], fillerB)
            ifPattern = ifPattern.close()

            statement = rdfsFormula.newFormula()
            statement.add(fillerB, environment[fillerA], fillerA)
            statement.close()
            description = SubstitutingList([rdfsFormula.newList([
                            environment[fillerA],
                            rdfsFormula.newLiteral(" is a symmetric property")])])
            patternAssertionThunk = makePatternAssertionThunk(triples, statement, description)

            bottomBeta = MM.compilePattern(index, ifPattern.statements, vars, rdfsFormula, supportBuiltin=False, reachedGoal=lambda: False)
            trueBottom =  MM.ProductionNode(bottomBeta, patternAssertionThunk, doNothing)

    # Search for owl:SymmetricProperty statements.
    ifPattern = rdfsFormula.newFormula()
    ifPattern.add(fillerA, rdf['type'], owl['SymmetricProperty'])
    ifPattern = ifPattern.close()
    bottomBeta = MM.compilePattern(index, ifPattern.statements, vars, rdfsFormula, supportBuiltin=False, reachedGoal=lambda: False)
    trueBottom =  MM.ProductionNode(bottomBeta, buildOWLSymmetricProperty, doNothing)

    # owl:TransitiveProperty
    def buildOWLTransitiveProperty((triples, environment, penalty)):
        """Build the non-goal rules for an owl:TransitiveProperty property."""
        # Assert rules in both goals and non-goals.
        for context in (tms.getContext(GOAL), tms.workingContext):
            index = context._index

            # :A :subj :B . :B :subj :C -> :A :subj :C
            ifPattern = rdfsFormula.newFormula()
            ifPattern.add(fillerA, environment[fillerA], fillerB)
            ifPattern.add(fillerB, environment[fillerA], fillerC)
            ifPattern = ifPattern.close()

            statement = rdfsFormula.newFormula()
            statement.add(fillerA, environment[fillerA], fillerC)
            statement.close()
            description = SubstitutingList([rdfsFormula.newList([
                            environment[fillerA],
                            rdfsFormula.newLiteral(" is a transitive property")])])
            patternAssertionThunk = makePatternAssertionThunk(triples, statement, description)

            bottomBeta = MM.compilePattern(index, ifPattern.statements, vars, rdfsFormula, supportBuiltin=False, reachedGoal=lambda: False)
            trueBottom =  MM.ProductionNode(bottomBeta, patternAssertionThunk, doNothing)

    # Search for owl:TransitiveProperty statements.
    ifPattern = rdfsFormula.newFormula()
    ifPattern.add(fillerA, rdf['type'], owl['TransitiveProperty'])
    ifPattern = ifPattern.close()
    bottomBeta = MM.compilePattern(index, ifPattern.statements, vars, rdfsFormula, supportBuiltin=False, reachedGoal=lambda: False)
    trueBottom =  MM.ProductionNode(bottomBeta, buildOWLTransitiveProperty, doNothing)

def testPolicy(logURIs, policyURIs, logFormula=None, ruleFormula=None, filterProperties=['http://dig.csail.mit.edu/TAMI/2007/amord/air#compliant-with', 'http://dig.csail.mit.edu/TAMI/2007/amord/air#non-compliant-with'], verbose=False, customBaseFactsURI=False, customBaseRulesURI=False):
    """Test a policy by running it and return the output justification as
    an N3 string."""
    trace, result = runPolicy(logURIs, policyURIs, logFormula=logFormula, ruleFormula=ruleFormula, filterProperties=filterProperties, verbose=verbose, customBaseFactsURI=customBaseFactsURI, customBaseRulesURI=customBaseRulesURI)
    return trace.n3String()

goalWildcards = {}

def mem(size="rss"):
    """ Generalization: memory sizes: rss, rsz, vsz. """
    return int(os.popen('ps -p %d -o %s | tail -1' % (os.getpid(),size)).read())

def runPolicy(logURIs, policyURIs, logFormula=None, ruleFormula=None, filterProperties=['http://dig.csail.mit.edu/TAMI/2007/amord/air#compliant-with', 'http://dig.csail.mit.edu/TAMI/2007/amord/air#non-compliant-with'], logFormulaObjs=[], ruleFormulaObjs=[], store=store, verbose=False, customBaseFactsURI=False, customBaseRulesURI=False):
    """Run a policy with the specified fact formula URIs, policy URIs,
    etc., generate a justification for any assertions concluded, and
    filter those justifications for assertions having a property that is
    one of the specified filterProperties.  Returns a Formula object
    containing the justification."""
    global baseFactsURI, baseRulesURI
    if OFFLINE[0]:
        baseFactsURI = uripath.join(uripath.base(),
                                      baseFactsURI.replace('http://dig.csail.mit.edu/TAMI',
                                                           '../../..'))
        baseRulesURI = uripath.join(uripath.base(),
                                      baseRulesURI.replace('http://dig.csail.mit.edu/TAMI',
                                                           '../../..'))
        logURIs = map(lambda x: uripath.join(uripath.base(), x), logURIs)
        policyURIs = map(lambda x: uripath.join(uripath.base(), x), policyURIs)
    elif customBaseFactsURI or customBaseRulesURI:
        baseFactsURI = customBaseFactsURI
        baseRulesURI = customBaseRulesURI
    import time
    formulaTMS = setupTMS(store)
    workingContext = formulaTMS.workingContext

## We are done with cwm setup
    startSize = mem("rss")
    startTime = time.time()
    
    logFormulae = []
    if logFormula is not None:
        logFormulae.append(loadFactN3(formulaTMS, logFormula, ""))
    for logURI in logURIs:
        logFormulae.append(loadFactFormula(formulaTMS, logURI, "")) # should it be "p"?
    for logFormulaObj in logFormulaObjs:
        logFormulae.append(loadFactFormulaObj(formulaTMS, logFormulaObj, ""))

#    baseFactsFormula = loadFactFormula(formulaTMS, baseFactsURI)

    eventLoop = EventLoop()


    policyFormulae = []
    if ruleFormula is not None:
        policyFormulae.append(parseN3(store, store.newFormula(), ruleFormula))
    for policyURI in policyURIs:
        policyFormulae.append(store.load(policyURI))
    for ruleFormulaObj in ruleFormulaObjs:
        fCopy = store.newFormula()
        fCopy.loadFormulaWithSubstitution(ruleFormulaObj, Env())
        policyFormulae.append(fCopy)
#    baseRulesFormula = store.load(baseRulesURI)

    # NOTE: We also need to parse every log for meaningful RDFS rules
    # to instantiate.
    makeRDFSRules(eventLoop, formulaTMS)

#    rdfsRulesFormula = store.load('http://python-dlp.googlecode.com/files/pD-rules.n3')
    
    rdf = workingContext.newSymbol('http://www.w3.org/1999/02/22-rdf-syntax-ns')
    owl = workingContext.newSymbol('http://www.w3.org/2002/07/owl')
    p = workingContext.newSymbol('http://dig.csail.mit.edu/TAMI/2007/amord/air')
    u = workingContext.newSymbol('http://dig.csail.mit.edu/TAMI/2007/s0/university')
    s9 = workingContext.newSymbol('http://dig.csail.mit.edu/TAMI/2007/s9/run/s9-policy')
    s9Log = workingContext.newSymbol('http://dig.csail.mit.edu/TAMI/2007/s9/run/s9-log')


#    AIRFormula = store.load(p.uriref() + '.ttl')
#    formulaTMS.getThing(AIRFormula).assume()
        
#    formulaTMS.getTriple(p['data'], rdf['type'], owl['TransitiveProperty']).assume()

    compileStartSize = mem("rss")
    compileStartTime = time.time()

    rdfsRules = [] #[Rule.compileCwmRule(eventLoop, formulaTMS, rdfsRulesFormula, x) for x in rdfsRulesFormula.statementsMatching(pred=store.implies)]


    # Add the filterProperties as goals through AuxTripleJustifier
    goalWildcards = {
        SUBJ: store.newSymbol(store.genId()),
        PRED: store.newSymbol(store.genId()),
        OBJ: store.newSymbol(store.genId())
        }
    for p in filterProperties:
        s = goalWildcards[SUBJ]
        p = store.newSymbol(p)
        o = goalWildcards[OBJ]
        eventLoop.add(AuxTripleJustifier(formulaTMS, True, s, p, o, frozenset([s, o]), None, []))

    allRules = []
    allGoalRules = []
#    # We need to 'flatten' the policy formulae before we can compile it.
    policyFormula = store.mergeFormulae(policyFormulae)
    for pf in [policyFormula]: # + [baseRulesFormula]:
#    for pf in policyFormulae + [baseRulesFormula]:
#        if pf is baseRulesFormula: ## Realy bad hack!
#            base=True
#        else:
#            base=False
        base = False
        policies, rules, goal_rules, cwm_rules = Rule.compileFormula(eventLoop, formulaTMS, pf, base=base, goalWildcards=goalWildcards)
        formulaTMS.assumedPolicies.extend(policies)
        allRules += rules
        allRules += cwm_rules
        allGoalRules += goal_rules
    if verbose:
        print 'rules = ', allRules
        print 'goal rules = ', goal_rules
    ruleAssumptions = []
    for rule in rdfsRules + allRules + allGoalRules:
        a  = formulaTMS.getThing(rule)
        ruleAssumptions.append(a)
        a.assume()
 
    eventStartSize = mem("rss")
    eventStartTime = time.time()
    Formula._isReasoning = True
    FormulaTMS.tracking = False
    while eventLoop:
        eventLoop.next()
    Formula._isReasoning = False
    if verbose:
        print workcount

# See how long it took (minus output)
    now = time.time()
    nowSize = mem("rss")

    totalTime = now - startTime
    totalSize = nowSize - startSize

    if verbose:
        print 'time reasoning took=', totalTime
        print '  of which %s was after loading, and %s was actual reasoning' % (now-compileStartTime, now-eventStartTime)
        print '  additionally, %s was in log:semantics, %s of which was parsing' % (llyn.total_time, llyn.load_time)

#    rete.printRete()
    if len(filterProperties) > 0:
        triples = list(reduce(lambda x, y: x + y, [workingContext.statementsMatching(pred=workingContext.newSymbol(property)) for property in filterProperties]))
    else:
        triples = workingContext.statements
    if verbose:
        if triples:
            print 'I can prove the following compliance statements:'
        else:
            print 'There is nothing to prove'
        
    tmsNodes = [formulaTMS.getTriple(triple.subject(), triple.predicate(), triple.object(), None) for triple in triples]
    reasons, premises = supportTrace(tmsNodes)
    reasons, premises = removeFormulae(reasons, premises)
    strings = simpleTraceOutput(tmsNodes, reasons, premises)
    if verbose:
        print '\n'.join(strings)
    f = rdfTraceOutput(store, tmsNodes, reasons, premises, formulaTMS.envs, Rule)
#    import diag
#    diag.chatty_flag = 1000
    return f, workingContext 


knownScenarios = {
    's0' : ( ['http://dig.csail.mit.edu/TAMI/2007/s0/log.n3'],
             ['http://dig.csail.mit.edu/TAMI/2007/s0/mit-policy.n3'] ),
    's0Local' : ( ['../../s0/log.n3'],
                  [  '../../s0/mit-policy.n3'] ),
    's9var2Local' : (['../../s9/variation2/log.n3'],
                     ['../../s9/variation2/policy.n3']),
    's9var1Local' : (['../../s9/variation1/log.n3'],
                     ['../../s9/variation1/policy1.n3', '../../s9/variation1/policy2.n3']),
#                     ['../../s9/variation1/policy.n3']),
#                     ['../../s9/variation1/demo-policy.n3']),
    'arl1Local' : (['../../../../2008/ARL/log.n3'],
                     ['../../../../2008/ARL/udhr-policy.n3']),    
     'arl2Local' : (['../../../../2008/ARL/log.n3'],
                     ['../../../../2008/ARL/unresol-policy.n3']),    
    's4' : (['http://dig.csail.mit.edu/TAMI/2006/s4/background.ttl',
'http://dig.csail.mit.edu/TAMI/2006/s4/categories.ttl',
'http://dig.csail.mit.edu/TAMI/2006/s4/data-schema.ttl',
'http://dig.csail.mit.edu/TAMI/2006/s4/fbi-bru.ttl',
'http://dig.csail.mit.edu/TAMI/2006/s4/fbi-crs.ttl',
'http://dig.csail.mit.edu/TAMI/2006/s4/fbi-tsrs.ttl',
'http://dig.csail.mit.edu/TAMI/2006/s4/purposes.ttl',
'http://dig.csail.mit.edu/TAMI/2006/s4/s4.ttl',
'http://dig.csail.mit.edu/TAMI/2006/s4/tsa-sfdb.ttl',
'http://dig.csail.mit.edu/TAMI/2006/s4/usms-win.ttl'
],
            ['http://dig.csail.mit.edu/TAMI/2006/s4/privacy-act.ttl']),
    'ucsd' : (['http://dig.csail.mit.edu/2008/Talks/0513-UCSD/simple/policy-explicit.n3'],
            ['http://dig.csail.mit.edu/2008/Talks/0513-UCSD/simple/data1.n3']),
    'arl1' : (['http://dig.csail.mit.edu/2008/ARL/log.n3'],
              ['http://dig.csail.mit.edu/2008/ARL/udhr-policy.n3']), 
    'arl2' : (['http://dig.csail.mit.edu/2008/ARL/log.n3'],
             ['http://dig.csail.mit.edu/2008/ARL/unresol-policy.n3']),
    'dhs' : (['http://dig.csail.mit.edu/2009/DHS-fusion/samples/request.n3'],
             ['http://dig.csail.mit.edu/2009/DHS-fusion/samples/uri-content.n3']),
    'dhs2' : (['http://dig.csail.mit.edu/2009/DHS-fusion/samples/request.n3'],
             ['http://dig.csail.mit.edu/2009/DHS-fusion/Mass/MGL_6-172/MGL_temp_1112.n3']),
    'privacy' : (['http://dig.csail.mit.edu/2009/DHS-fusion/PrivacyAct/log.n3'], ['http://dig.csail.mit.edu/2009/DHS-fusion/PrivacyAct/policy.n3']),
    'idm' : (['http://dig.csail.mit.edu/2010/DHS-fusion/MA/rules/MGL_6-172_ONT.n3',
              'file://' + os.path.abspath(os.path.join(os.path.realpath(__file__), '../../tests/mia_analysa.rdf')),
              'file://' + os.path.abspath(os.path.join(os.path.realpath(__file__), '../../tests/frederick_agenti.rdf')),
              'http://dice.csail.mit.edu/xmpparser.py?uri=http://dice.csail.mit.edu/idm/MA/documents/Fake_MA_Request.pdf',
              'http://dice.csail.mit.edu/idm/MA/rules/mgl_sameAs.n3',
              'file://' + os.path.abspath(os.path.join(os.path.realpath(__file__), '../../tests/idm_nonce.n3'))],
             ['http://dice.csail.mit.edu/idm/MA/rules/MGL_6-172.n3']),
    'millie' : (['http://dig.csail.mit.edu/2010/DHS-fusion/MA/rules/MGL_6-172_ONT.n3',
              'http://dig.csail.mit.edu/2010/DHS-fusion/MA/profiles/MiaAnalysa',
              'http://dig.csail.mit.edu/2010/DHS-fusion/MA/profiles/MillieRecruiting',
              'http://dice.csail.mit.edu/xmpparser.py?uri=http://dig.csail.mit.edu/2010/DHS-fusion/MA/documents/Fake_Recruiter_Response.badxmp.pdf',
              'http://dig.csail.mit.edu/2010/DHS-fusion/MA/rules/MGL_66A-1_ONT.n3',
              'http://dig.csail.mit.edu/2010/DHS-fusion/common/fusion_ONT.n3',
              'http://dig.csail.mit.edu/2010/DHS-fusion/MA/rules/mgl_sameAs.n3',
              'file://' + os.path.abspath(os.path.join(os.path.realpath(__file__), '../../tests/millie_nonce.n3'))],
             ['http://dig.csail.mit.edu/2010/DHS-fusion/MA/rules/MGL_6-172.alt.n3']),
}

def runScenario(s, others=[], verbose=False, customBaseRulesURI=False, customBaseFactsURI=False):
    if s[-5:] == 'Local':
        OFFLINE[0] = True
    if s == 'test':
        rules = others[0:1]
        facts = others[1:]
    elif s == 'list':
        for el in others:
            if el[0:5] == 'rules': rules = el[7:-1].replace(',',' ').split()
            elif el[0:4] == 'data': facts = el[6:-1].replace(',',' ').split()
    elif s not in knownScenarios:
        facts = ['http://dig.csail.mit.edu/TAMI/2007/%s/log.n3' % s]
        rules = ['http://dig.csail.mit.edu/TAMI/2007/%s/policy.n3' % s]
 #       raise ValueError("I don't know about scenario %s" % s)
    else:
        facts, rules = knownScenarios[s]
    return testPolicy(facts, rules, verbose=verbose, customBaseRulesURI=customBaseRulesURI, customBaseFactsURI=customBaseFactsURI)

def main():
    global MM
    from optparse import OptionParser
    usage = "usage: %prog [options] scenarioName\n"
    usage + "       %prog [options] test rulesFile logsFile+"
    parser = OptionParser(usage)
    parser.add_option('--profile', dest="profile", action="store_true", default=False,
                      help="""Instead of displaying output, display profile information.
 This requires the hotshot module, and is a bit slow
""")
    parser.add_option('--psyco', '-j', dest='psyco', action="store_true", default=False,
                      help="""Try to use psyco to speed up the program.
Don't try to do this while profiling --- it won't work.
If you do not have psyco, it will throw an ImportError

There are no guarentees this will speed things up instead of
slowing them down. In fact, it seems to slow them down quite
a bit right now
""")
    parser.add_option('--full-unify', dest='fullUnify', action="store_true", default=False,
                      help="""Run full unification (as opposed to simple implication)
of goals. This may compute (correct) answers it would otherwise miss
It is much more likely to simply kill your performance at this time
""")
    parser.add_option('--lookup-ontologies', '-L', dest="lookupOntologies", action="store_true", default=False,
                      help="""Set the cwm closure flag of "p" on all facts loaded.
This will load the ontologies for all properties, until that
converges. This may compute (correct) answers it would otherwise miss
It is much more likely to simply kill your performance at this time.
It may even cause the computation to fail, if a URI 404's or is not RDF.
""")
    parser.add_option('--reasoner', '-r', dest="reasoner", action="store", default="rete",
                      help="""Which reasoner to chose. Current options are
'rete' and 'treat' (without the quotes). The default is 'rete',
which seems faster right now. 'treat' is likely more extensible
for the future, but may still be buggy.
""")
    parser.add_option('--verbose', '-v', dest="verbose", action="store_true", default=False,
                      help="""\"Oh policyrunner, why don't you talk to me the way you used to?\"""")
    parser.add_option('--base-rules', '-R', dest="customBaseRulesURI", action="store", default=False,
                      help="""Set the base rules URI.""")
    parser.add_option('--base-facts', '-F', dest="customBaseFactsURI", action="store", default=False,
                      help="""Set the base facts URI.""")

    (options, args) = parser.parse_args()
    if not args:
        args = ['s0']
    call = lambda : runScenario(args[0], args[1:], options.verbose, options.customBaseRulesURI, options.customBaseFactsURI)
    MM = eval(options.reasoner)
    if options.lookupOntologies:
        loadFactFormula.pClosureMode = True
    if options.fullUnify:
        rete.fullUnify = True
    if options.psyco:
        if options.profile:
            raise ValueError("I can't profile with psyco")
        import psyco
        psyco.log()
        psyco.full()
##        psyco.profile(0.05, memory=100)
##        psyco.profile(0.2)
    if options.profile:
        import sys
        stdout = sys.stdout
        import hotshot, hotshot.stats
        import tempfile
        fname = tempfile.mkstemp()[1]
        if options.verbose:
            print fname
        sys.stdout = null = file('/dev/null', 'w')
        profiler = hotshot.Profile(fname)
        profiler.runcall(call)
        profiler.close()
        sys.stdout = stdout
        null.close()
        if options.verbose:
            print 'done running. Ready to do stats'
        stats = hotshot.stats.load(fname)
        stats.strip_dirs()
        stats.sort_stats('cumulative', 'time', 'calls')
        stats.print_stats(60)
        stats.sort_stats('time', 'cumulative', 'calls')
        stats.print_stats(60)
    else:
        print call().encode("utf-8")

        


if __name__ == '__main__':
    main()

