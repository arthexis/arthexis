with Arthexis.ORM;

package body Apps.Views.Functions.Views_Functions is

   procedure Install (Conn : in out Arthexis.ORM.Database_Connection) is
   begin
      Arthexis.ORM.Execute
        (Conn,
         "CREATE VIEW IF NOT EXISTS views_view_index AS "
         & "SELECT app_name, view_name "
         & "FROM views_view "
         & "ORDER BY app_name, view_name;");
   end Install;

end Apps.Views.Functions.Views_Functions;
