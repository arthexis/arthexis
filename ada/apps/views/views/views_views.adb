package body Apps.Views.Views.Views_Views is

   function View_Index_SQL return String is
   begin
      return
        "SELECT id, app_name, view_name "
        & "FROM views_view "
        & "ORDER BY app_name, view_name";
   end View_Index_SQL;

end Apps.Views.Views.Views_Views;
