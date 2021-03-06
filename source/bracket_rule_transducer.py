from rule import DELETION, INSERTION, DEGENERATE, ASSIMILATION, NO_CONTEXT, BOTH_CONTEXTS, LEFT_CONTEXT_ONLY, \
    RIGHT_CONTEXT_ONLY
from segment_table import SegmentTable
import fst
from utils.cache import Cache
from fst import EPSILON
from tests.test_util import write_to_dot_to_file as dot
from automata.pyfst_fado_interface import pyfst_from_dfa, pyfst_to_dfa
from configuration import Configuration
from multiprocessing import current_process
configurations = Configuration()
from util import safe_compose, chain_safe_compose, get_transducer_outputs
from utils.environment import get_process_number
from FAdo.reex import str2regexp as fado_str2regexp
from FAdo.reex import ParseReg1
from rule import get_context_regex
from segment_table import LEFT_APPLICATION_BRACKET, LEFT_CENTER_BRACKET, LEFT_IDENTITY_BRACKET
from segment_table import RIGHT_APPLICATION_BRACKET, RIGHT_CENTER_BRACKET, RIGHT_IDENTITY_BRACKET

LEFT_BRACKETS = [LEFT_APPLICATION_BRACKET, LEFT_CENTER_BRACKET, LEFT_IDENTITY_BRACKET]
RIGHT_BRACKETS = [RIGHT_APPLICATION_BRACKET, RIGHT_CENTER_BRACKET, RIGHT_IDENTITY_BRACKET]
BRACKETS = RIGHT_BRACKETS + LEFT_BRACKETS


cache = Cache()
right_context_dfas = dict()
left_context_dfas = dict()
rule_transducers = dict()


# Wrappers for FAdo.reex.ParseReg1 and str2regexp().
# Needed when running multiple processes of simulations since dbm writes to the same shelve file by default.
class ParseReg1MultiProcess(ParseReg1):
    def __init__(self, no_table=0, table=None):
        super(ParseReg1MultiProcess, self).__init__(no_table=no_table, table=BracketRuleTransducer.get_table_name())


def str2regexp(s, parser=ParseReg1MultiProcess, no_table=1, sigma=None, strict=False):
    cached = cache.get(s, 'str2rgx')
    if cached is not None:
        return cached
    rgx = fado_str2regexp(s, parser=parser, no_table=no_table, sigma=sigma, strict=strict)
    cache.set(s, rgx, 'str2rgx')
    return rgx


class BracketRuleTransducer:
    def __init__(self, rule):
        self.__dict__.update(rule.__dict__)
        self.alphabet = set(SegmentTable().get_segments_symbols())

    def _get_left_context_dfa(self):
        left_context_key = str(self.left_context_feature_bundle_list)
        if left_context_key in left_context_dfas:
            return left_context_dfas[left_context_key]

        alphabet = self.alphabet
        sigma_star_dfa = sigma_star_dfa_for_left_context
        if self.left_context_feature_bundle_list:
            context_regex = get_context_regex(self.left_context_feature_bundle_list)
            if configurations["LENGTHENING_FLAG"]:
                context_regex = context_regex + "(Y)*"
            left_context_dfa = str2regexp(context_regex, sigma=alphabet).toDFA()
            left_context_dfa_ignore_L = get_ignore_dfa(alphabet | set(LEFT_BRACKETS), left_context_dfa,
                                                       set(LEFT_BRACKETS))
            sigma_star_left_context_dfa = sigma_star_dfa.concat(left_context_dfa_ignore_L)
        else:
            sigma_star_left_context_dfa = sigma_star_dfa

        left_brackets_regex = "({})".format("+".join(LEFT_BRACKETS))
        left_bracket_dfa = get_dfa_from_regex(left_brackets_regex, sigma=LEFT_BRACKETS)

        sigma_star_L = sigma_star_dfa.concat(left_bracket_dfa)

        sigma_star_L_complement = ~sigma_star_L
        subtraction_result = sigma_star_left_context_dfa & sigma_star_L_complement

        L_sigma_star = left_bracket_dfa.concat(sigma_star_dfa)

        p_iff_s_dfa = get_p_iff_s_dfa(subtraction_result, L_sigma_star)

        p_iff_s_ignore_right_bracket = get_ignore_dfa(alphabet | set(BRACKETS), p_iff_s_dfa, set(RIGHT_BRACKETS))

        left_context_dfa = p_iff_s_ignore_right_bracket
        left_context_dfa = pyfst_from_dfa(left_context_dfa)
        left_context_dfas[left_context_key] = left_context_dfa
        return left_context_dfa

    def _get_right_context_dfa(self):
        right_context_key = str(self.right_context_feature_bundle_list)
        if right_context_key in right_context_dfas:
            return right_context_dfas[right_context_key]

        alphabet = self.alphabet
        sigma_star_dfa = sigma_star_dfa_for_right_context

        if self.right_context_feature_bundle_list:
            right_context_dfa = get_context_dfa(self.right_context_feature_bundle_list)
            right_context_dfa_ignore_R = get_ignore_dfa(alphabet | set(RIGHT_BRACKETS), right_context_dfa,
                                                        set(RIGHT_BRACKETS))
            right_context_sigma_star_dfa = right_context_dfa_ignore_R.concat(sigma_star_dfa)
        else:
            right_context_sigma_star_dfa = sigma_star_dfa

        right_brackets_regex = "({})".format("+".join(RIGHT_BRACKETS))
        right_bracket_acceptor = get_dfa_from_regex(right_brackets_regex, sigma=RIGHT_BRACKETS)
        sigma_star_R = sigma_star_dfa.concat(right_bracket_acceptor)

        R_sigma_star = right_bracket_acceptor.concat(sigma_star_dfa)
        R_sigma_star_complement = ~R_sigma_star

        subtraction_result = right_context_sigma_star_dfa & R_sigma_star_complement

        p_iff_s_dfa = get_p_iff_s_dfa(sigma_star_R, subtraction_result)

        p_iff_s_ignore_left_bracket = get_ignore_dfa(alphabet | set(BRACKETS), p_iff_s_dfa, set(LEFT_BRACKETS))

        right_context_dfa = p_iff_s_ignore_left_bracket
        right_context_dfa = pyfst_from_dfa(right_context_dfa)
        right_context_dfas[right_context_key] = right_context_dfa
        return right_context_dfa

    def get_replace_transducer(self):
        transducer_symbol_table = SegmentTable().transducer_symbol_table
        inner_replace_transducer = fst.Transducer(isyms=transducer_symbol_table, osyms=transducer_symbol_table)
        for segment1, segment2 in self.target_change_tuples_list:
            inner_replace_transducer.add_arc(0, 1, segment1, segment2)
        inner_replace_transducer[1].final = True
        inner_replace_transducer_ignore_brackets = [LEFT_CENTER_BRACKET, RIGHT_CENTER_BRACKET]

        for bracket in inner_replace_transducer_ignore_brackets:
            inner_replace_transducer.add_arc(0, 0, bracket, bracket)
            inner_replace_transducer.add_arc(1, 1, bracket, bracket)

        opt_part = left_bracket_transducer + inner_replace_transducer + right_bracket_transducer
        add_opt(opt_part)

        sigma_star_regex = "({})*".format("+".join(self.alphabet))
        sigma_star_dfa = get_dfa_from_regex(sigma_star_regex, sigma=self.alphabet)
        sigma_star_dfa_ignore_identity = get_ignore_dfa(
            self.alphabet | set([LEFT_IDENTITY_BRACKET, RIGHT_IDENTITY_BRACKET]), sigma_star_dfa,
            set([LEFT_IDENTITY_BRACKET, RIGHT_IDENTITY_BRACKET]))
        id_sigma_star = pyfst_from_dfa(sigma_star_dfa_ignore_identity)

        concat_transducer = id_sigma_star + opt_part
        replace_transducer = concat_transducer.closure()
        # dot(replace_transducer, "replace_transducer")
        return replace_transducer

    def _obligatory_maker(self, middle_dfa, left_brackets, right_brackets):
        alphabet = self.alphabet
        sigma_star_dfa = sigma_star_dfa_for_obligatory

        left_brackets_regex = "({})".format("+".join(left_brackets))
        right_brackets_regex = "({})".format("+".join(right_brackets))

        left_bracket_dfa = get_dfa_from_regex(left_brackets_regex, sigma=alphabet)
        right_bracket_dfa = get_dfa_from_regex(right_brackets_regex, sigma=alphabet)

        if middle_dfa:
            concat_result = chain_concat(sigma_star_dfa, left_bracket_dfa, middle_dfa, right_bracket_dfa,
                                         sigma_star_dfa)
        else:
            concat_result = chain_concat(sigma_star_dfa, left_bracket_dfa, right_bracket_dfa, sigma_star_dfa)

        complement_result = ~concat_result
        obligatory_dfa = complement_result

        return obligatory_dfa

    def _get_custom_obligatory_dfa(self, left_brackets, right_brackets):
        return self._obligatory_maker(None, left_brackets, right_brackets)

    def _get_obligatory_dfa(self, left_brackets, right_brackets):
        if self.transformation_type == INSERTION:
            middle_dfa = None
        else:
            if self.transformation_type == DELETION:
                segments_to_delete = self.target_segments
                segments_regex = "({})".format("+".join(segments_to_delete))
            elif self.transformation_type == ASSIMILATION:
                segments = []
                for target, change in self.target_change_tuples_list:
                    segments.append(target)
                segments_regex = "({})".format("+".join(segments))

            middle_dfa = get_dfa_from_regex(segments_regex, sigma=alphabet)
            middle_dfa = get_ignore_dfa(alphabet | set(BRACKETS), middle_dfa, set(BRACKETS))

        return self._obligatory_maker(middle_dfa, left_brackets, right_brackets)

    def get_left_to_right_application(self):
        prologue_transducer = get_prologue_transducer()
        if self.obligatory:
            obligatory_transducer = pyfst_from_dfa(
                self._get_obligatory_dfa([LEFT_IDENTITY_BRACKET], [RIGHT_IDENTITY_BRACKET]))
            composed_transducer = safe_compose(prologue_transducer, obligatory_transducer)
        else:
            composed_transducer = prologue_transducer

        right_context_transducer = self._get_right_context_dfa()
        replace_transducer = self.get_replace_transducer()
        left_context_transducer = self._get_left_context_dfa()
        prologue_inverse_transducer = get_prologue_inverse_transducer()

        composed_transducer = chain_safe_compose(composed_transducer, right_context_transducer, replace_transducer,
                                                 left_context_transducer)

        if self.transformation_type == INSERTION:
            insertion_obligatory_transducer = pyfst_from_dfa(
                self._get_obligatory_dfa([RIGHT_IDENTITY_BRACKET], [LEFT_IDENTITY_BRACKET]))
            composed_transducer = safe_compose(composed_transducer, insertion_obligatory_transducer)

        # remove multiple paths
        if self.transformation_type == ASSIMILATION or self.transformation_type == INSERTION:
            RI_obligatory_transducer = pyfst_from_dfa(
                self._get_custom_obligatory_dfa([RIGHT_APPLICATION_BRACKET], [LEFT_IDENTITY_BRACKET]))
            composed_transducer = safe_compose(composed_transducer, RI_obligatory_transducer)
        if self.transformation_type == DELETION:
            JL_obligatory_transducer = pyfst_from_dfa(
                self._get_custom_obligatory_dfa([RIGHT_IDENTITY_BRACKET], [LEFT_APPLICATION_BRACKET]))
            composed_transducer = safe_compose(composed_transducer, JL_obligatory_transducer)

        composed_transducer = safe_compose(composed_transducer, prologue_inverse_transducer)
        return composed_transducer

    @staticmethod
    def clear_caching():
        global rule_transducers
        rule_transducers = dict()

    def get_transducer(self):
        rule_key = str(self)
        if rule_key in rule_transducers:
            return rule_transducers[rule_key]
        else:
            transducer = self.get_left_to_right_application()
            rule_transducers[rule_key] = transducer
            return transducer

    @staticmethod
    def get_table_name():
        return ".tablereg_{}".format(get_process_number())

    def __repr__(self):
        return u"{} --> {}  /  {}__{} obligatory: {}".format(self.target_feature_bundle_list,
                                                             self.change_feature_bundle_list,
                                                             self.left_context_feature_bundle_list,
                                                             self.right_context_feature_bundle_list,
                                                             self.obligatory)


def get_prologue_transducer():
    alphabet = set(SegmentTable().get_segments_symbols())
    prologue_transducer = get_intro_transducer(alphabet, set(BRACKETS))
    return prologue_transducer


def get_prologue_inverse_transducer():
    transducer_symbol_table = SegmentTable().transducer_symbol_table
    prologue_inverse_transducer = fst.Transducer(isyms=transducer_symbol_table, osyms=transducer_symbol_table)
    alphabet = set(SegmentTable().get_segments_symbols())
    for segment in alphabet:
        prologue_inverse_transducer.add_arc(0, 0, segment, segment)
    for bracket in BRACKETS:
        prologue_inverse_transducer.add_arc(0, 0, bracket, EPSILON)
    prologue_inverse_transducer[0].final = True
    return prologue_inverse_transducer


def get_ignore_dfa(sigma, language_dfa, ignored_set):
    new_sigma = sigma | ignored_set
    intro_transducer = get_intro_transducer(sigma, ignored_set)
    language_transducer = pyfst_from_dfa(language_dfa)
    composed_transducer = safe_compose(language_transducer, intro_transducer)
    language_dfa = pyfst_to_dfa(composed_transducer, new_sigma)
    return language_dfa


def get_if_p_then_s_dfa(dfa1, dfa2):
    dfa2_complement = ~dfa2
    concat_result = dfa1.concat(dfa2_complement)
    concat_complement = ~concat_result
    return concat_complement


def get_if_s_then_p_dfa(dfa1, dfa2):
    dfa1_complement = ~dfa1
    concat_result = dfa1_complement.concat(dfa2)
    concat_complement = ~concat_result
    return concat_complement


def get_p_iff_s_dfa(dfa1, dfa2):
    p_then_s = get_if_p_then_s_dfa(dfa1, dfa2)
    s_then_p = get_if_s_then_p_dfa(dfa1, dfa2)
    intersected = p_then_s & s_then_p
    return intersected


def get_dfa_from_regex(regex, sigma=None):
    regex_key = regex + str(sigma)
    cached = cache.get(regex_key, 'dfa_rgx')
    if cached is not None:
        return cached

    try:
        dfa = str2regexp(regex, sigma=sigma, no_table=1).toDFA()
    except:
        print(regex)
        raise ValueError

    cache.set(regex_key, dfa, 'dfa_rgx')
    return dfa


def chain_concat(*dfas):
    concat_result = dfas[0]
    for dfa in dfas[1:]:
        concat_result = concat_result.concat(dfa)
    return concat_result


def add_opt(transducer):
    initial_state = None
    final_state = None
    for i in range(len(transducer)):
        if transducer[i].initial:
            initial_state = i
        if transducer[i].final:
            final_state = i
    transducer.add_arc(initial_state, final_state, 0, 0)


def get_context_dfa(context_features):
    alphabet = set(SegmentTable().get_segments_symbols())
    context_regex = get_context_regex(context_features)
    context_dfa = str2regexp(context_regex, sigma=alphabet).toDFA()
    return context_dfa


def get_intro_transducer(sigma, introduced_set):
    sigma_transducer = get_sigma_transducer_for_intro(sigma)

    transducer_symbol_table = SegmentTable().transducer_symbol_table
    cartesian_transducer = fst.Transducer(isyms=transducer_symbol_table, osyms=transducer_symbol_table)
    for introduced_symbol in introduced_set:
        cartesian_transducer.add_arc(0, 0, EPSILON, introduced_symbol)
    cartesian_transducer[0].final = True
    union_transducer = sigma_transducer | cartesian_transducer
    intro_transducer = union_transducer.closure()
    return intro_transducer


sigma_transducer_dict = dict()


def get_sigma_transducer_for_intro(sigma):
    sigma_key = "".join(sorted(list(sigma)))
    if sigma_key not in sigma_transducer_dict:
        sigma_regex = "({})".format("+".join(sigma))
        sigma_dfa = get_dfa_from_regex(sigma_regex, sigma=sigma)
        sigma_transducer_dict[sigma_key] = pyfst_from_dfa(sigma_dfa)
    return sigma_transducer_dict[sigma_key]


alphabet = set(SegmentTable().get_segments_symbols())

m_sigma_star_regex = "({})*".format("+".join(alphabet))
m_sigma_star_dfa = get_dfa_from_regex(m_sigma_star_regex, sigma=alphabet)

sigma_star_dfa_for_left_context = get_ignore_dfa(alphabet | set(LEFT_BRACKETS), m_sigma_star_dfa, set(LEFT_BRACKETS))
sigma_star_dfa_for_right_context = get_ignore_dfa(alphabet | set(RIGHT_BRACKETS), m_sigma_star_dfa, set(RIGHT_BRACKETS))
sigma_star_dfa_for_obligatory = get_ignore_dfa(alphabet | set(BRACKETS), m_sigma_star_dfa, set(BRACKETS))

left_bracket_transducer = pyfst_from_dfa(get_dfa_from_regex(LEFT_APPLICATION_BRACKET).toDFA())
right_bracket_transducer = pyfst_from_dfa(get_dfa_from_regex(RIGHT_APPLICATION_BRACKET).toDFA())
