# TESTED IN V13 (MIGHT NEED TO BE ADAPTED IN OTHER VERSIONS)
# When using the script for entry_type move (miscellaneous, bank, ...), pay attention to the result and double check.
# The modification regarding those could be tricky:
# if the move_type is 'entry', we miss the move_type information to decide which repartition_line to take from the tax.
# We had to make a choice and thus, we assume it is an in/out invoice..
# Also, the script doesn't pay attention to cash basis and thus could need some fine tuning.
# Have a nice fix !
# Authored by BIB, inspired by ALT

# This fix has been made to correct the taxes of l10n_NL which contains the tax_grid 4b and 4a
# and to correct the journal items that are impacted posterior to a certain date
# /!\ RE-USABILITY WARNING:
# This script isn't a magic shotgun, the function get_new_tax_grid is just correcting the taxes this way:
# if invoice and factor < 0 return the minus tax_grid tag
# if refund and factor < 0 return the plus tax_grid tag
# It only modifies tax_grid (account_tag) that are in the TAG_NAMES_TO_FIX


TAG_NAMES_TO_FIX = ['4a (BTW)', '4b (BTW)']
# put it to True if you want to update your account_move_line
# You typically want to update the amls to impact your reports.
# If the report has been sent to the state it shouldn't be updated but with good reason, it could be useful.
# Thus you should warn the client and get him to confirm he wants the update to happen.
IS_UPDATING_AML = False
# Set move ids for which you don't want the grid of its amls to update
EXCLUDED_MOVES_IDS = []
# Set to true if you want to force the tax grid update for ALL entry move (excluding excluded move)
IS_ENTRY_TYPE_MOVE_FORCED = False
# Set the date from which you want to apply the update in the bracket
CORRECTION_FROM_DATE = 'YYYY-MM-DD'  # YYYY-MM-DD

# main()
def fix_taxes_and_modify_aml_to_tax_grid_relation():
    tax_ids = fix_and_get_taxes_related_to_tag_names()
    if IS_UPDATING_AML:
        aml_ids = fix_tax_grid_on_existing_aml(tax_ids)
        update_tax_audit_string(aml_ids)

def fix_and_get_taxes_related_to_tag_names():
    report_lines = env['account.tax.report.line'].search([('tag_name', 'in', TAG_NAMES_TO_FIX)])
    repartition_lines_to_correct = get_repartition_lines_to_correct(report_lines)
    correct_repartition_line_tag(repartition_lines_to_correct, report_lines.tag_ids)
    return repartition_lines_to_correct.tax_id.ids

def fix_tax_grid_on_existing_aml(tax_ids):
    repartition_lines_ids = get_repartition_lines_ids(tax_ids)
    check_and_warn_for_entry_type_move(repartition_lines_ids)
    modified_aml_ids = modify_tag_to_aml_relation(repartition_lines_ids)
    return modified_aml_ids

def update_tax_audit_string(aml_ids):
    env['account.move.line'].browse(aml_ids)._compute_tax_audit()

def get_repartition_lines_to_correct(report_lines):
    # get the repartition line with the concerned tax_grid for which tax negate isn't set correctly
    repartition_lines_to_correct_filter = [
        ('tag_ids', 'in', report_lines.tag_ids.ids),
        ('factor_percent', '<', '0'),
        '|',
        '&', ('invoice_tax_id', '!=', False), ('tag_ids.tax_negate', '=', False),
        '&', ('refund_tax_id', '!=', False), ('tag_ids.tax_negate', '=', True)
    ]
    return env['account.tax.repartition.line'].search(repartition_lines_to_correct_filter)

def correct_repartition_line_tag(repartition_lines_to_correct, tax_grid_ids):
    for repartition_line in repartition_lines_to_correct:
        # keep only the tax ids concerned
        current_tax_grid = repartition_line.tag_ids & tax_grid_ids
        new_tax_grid = get_new_tax_grid(repartition_line, current_tax_grid)
        tag_ids_modification = get_tag_modification(current_tax_grid, new_tax_grid)
        if tag_ids_modification:
            repartition_line.write({'tag_ids': tag_ids_modification})

def get_repartition_lines_ids(tax_ids):
    # fall back if the taxes were already corrected
    if not tax_ids:
        report_lines = env['account.tax.report.line'].search([('tag_name', 'in', TAG_NAMES_TO_FIX)])
        repartition_lines_to_correct_filter = [
            ('tag_ids', 'in', report_lines.tag_ids.ids),
            ('factor_percent', '<', '0')
        ]
        tax_ids = env['account.tax.repartition.line'].search(repartition_lines_to_correct_filter).tax_id.ids

    taxes = env['account.tax'].browse(tax_ids)
    return tuple(taxes.invoice_repartition_line_ids.ids + taxes.refund_repartition_line_ids.ids)

def check_and_warn_for_entry_type_move(repartition_lines_ids):
    env.cr.execute("""
        SELECT array_agg(move.id)
        FROM account_account_tag_account_move_line_rel aml_tags
        INNER JOIN account_move_line aml ON aml_tags.account_move_line_id = aml.id
        INNER JOIN account_move move ON aml.move_id=move.id
        WHERE move.type = 'entry'
        AND aml.tax_repartition_line_id in %(rep_ln_ids)s
        AND aml.date >= %(correction_from_date)s;
    """, {'rep_ln_ids': repartition_lines_ids, 'correction_from_date': CORRECTION_FROM_DATE})

    entry_moves = env.cr.fetchone()[0] or []

    if set(entry_moves) - set(EXCLUDED_MOVES_IDS) and not IS_ENTRY_TYPE_MOVE_FORCED:
        raise Warning("Some entry_moves operations use taxes in this database (%s). Changing their tags is trickier."
                      % (list(set(entry_moves) - set(EXCLUDED_MOVES_IDS))))

def modify_tag_to_aml_relation(repartition_lines_ids):
    env.cr.execute("""
        DELETE FROM account_account_tag_account_move_line_rel aml_tags
        USING account_move_line aml, account_move move
        WHERE aml.tax_repartition_line_id IN %(rep_ln_ids)s
        AND aml.id = aml_tags.account_move_line_id
        AND aml.move_id = move.id
        AND move.id != ALL(%(excluded_move_ids)s) 
        AND (move.type != 'entry' OR %(is_entry_type_move_forced)s)
        AND aml.date >= %(correction_from_date)s;

        INSERT INTO account_account_tag_account_move_line_rel
            SELECT aml.id as account_move_line_id, rep_tags.account_account_tag_id AS account_account_tag_id
            FROM account_move_line aml
            JOIN account_account_tag_account_tax_repartition_line_rel rep_tags
            ON rep_tags.account_tax_repartition_line_id = aml.tax_repartition_line_id
            JOIN account_tax_repartition_line rep_ln
            ON rep_ln.id = aml.tax_repartition_line_id
            JOIN account_move move 
            ON aml.move_id = move.id
            WHERE (
                (
                    move.type IN ('in_refund', 'out_refund') 
                    AND rep_ln.refund_tax_id IS NOT NULL
                ) 
                OR rep_ln.invoice_tax_id IS NOT NULL
            ) -- By default, on misc operations, we take invoice repartition
            AND move.id != ALL(%(excluded_move_ids)s) 
            AND (move.type != 'entry' OR %(is_entry_type_move_forced)s)
            AND rep_ln.id IN %(rep_ln_ids)s
            AND aml.date >= %(correction_from_date)s
        RETURNING account_move_line_id;
    """, {
        'rep_ln_ids': repartition_lines_ids,
        'excluded_move_ids': EXCLUDED_MOVES_IDS,
        'is_entry_type_move_forced': IS_ENTRY_TYPE_MOVE_FORCED,
        'correction_from_date': CORRECTION_FROM_DATE,
    })
    return [row[0] for row in env.cr.fetchall()]

def get_new_tax_grid(repartition_line, current_tax_grid):
    new_tax_grid = ()
    if repartition_line.invoice_tax_id:
        new_tax_grid = current_tax_grid.tax_report_line_ids.tag_ids.filtered('tax_negate')

    if repartition_line.refund_tax_id:
        new_tax_grid = current_tax_grid.tax_report_line_ids.tag_ids.filtered(lambda tax: not tax.tax_negate)
    return new_tax_grid

def get_tag_modification(current_tax_grid, new_tax_grid):
    to_unlink = [(3, tax_grid.id, 0) for tax_grid in current_tax_grid]
    to_link = [(4, tax_grid.id, 0) for tax_grid in new_tax_grid]
    return to_unlink + to_link


fix_taxes_and_modify_aml_to_tax_grid_relation()
